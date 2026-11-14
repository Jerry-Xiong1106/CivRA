"""Source-preserving document parsing and conservative field extraction."""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version as package_version

from pydantic import ValidationError

from .assets import CaseAssetStore
from .contracts import ExtractedText, ParseDocumentsData, ParseDocumentsRequest
from .field_extraction import extract_labelled_fields
from .models import BusinessError, BusinessResult, Evidence, MaterialRef
from .ocr import OcrEngine
from .repository import CaseNotFound, VersionConflict


class DocumentParser:
    def __init__(self, assets: CaseAssetStore, *, ocr: OcrEngine | None, max_pages=50, low_confidence=0.85):
        self.assets, self.ocr = assets, ocr
        self.max_pages, self.low_confidence = max_pages, low_confidence

    def parse_documents(self, request: ParseDocumentsRequest | dict) -> BusinessResult[ParseDocumentsData]:
        case_id, version = None, None
        try:
            request=ParseDocumentsRequest.model_validate(request.model_dump(mode="json") if isinstance(request,ParseDocumentsRequest) else request)
            case_id,version=request.context.case_id,request.case_version
            current=self.assets.repository.get_case(case_id)
            if current.case_version != version:
                raise VersionConflict(version,current.case_version)
            documents,segments,facts,warnings=[],[],[],[]
            for file_id in request.file_ids:
                if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                    warnings.append("已达到解析截止时间，未完成的文件需要重试。")
                    break
                try:
                    evidence_versions={item.file_version for item in current.evidence if item.file_id==file_id}
                    selected_version=request.file_versions.get(file_id)
                    if selected_version is None:
                        selected_version=next(iter(evidence_versions)) if len(evidence_versions)==1 else None
                    record,content=self.assets.get_file(case_id,file_id,selected_version)
                    file_warnings=[]
                    file_segments=self._parse(record,content,request.ocr_mode,
                        warnings=file_warnings,deadline_at=request.context.deadline_at)
                    if not file_segments:
                        warnings.extend(file_warnings)
                        warnings.append(f"{file_id}: 未提取到文字，不能当作材料为空。")
                        continue
                    evidence_id="e_"+hashlib.sha256((file_id+":"+record.file_version).encode()).hexdigest()[:20]
                    extracted,field_warnings=extract_labelled_fields(record,evidence_id,file_segments)
                    file_warnings.extend(field_warnings)
                    file_warnings.extend(warning for segment in file_segments for warning in segment.warnings)
                    file_warnings=list(dict.fromkeys(file_warnings))
                    documents.append(Evidence(evidence_id=evidence_id,file_id=file_id,file_version=record.file_version,
                        file_name=record.file_name,media_type=record.media_type,document_type=record.document_type,
                        content_sha256=record.content_sha256,material_refs=[segment.material_ref for segment in file_segments],
                        proves_fact_ids=[fact.fact_id for fact in extracted],status="unreviewed",
                        warnings=file_warnings))
                    segments.extend(file_segments)
                    facts.extend(extracted)
                    warnings.extend(file_warnings)
                except Exception as exc:
                    # A malformed file or an engine error must not hide other files' results.
                    # Interrupts and process exits are deliberately not caught here.
                    warnings.append(f"{file_id}: {type(exc).__name__}: {str(exc)[:160]}")
            if not documents:
                return self._error(case_id,version,"tool_error","未成功解析任何文件。",{"warnings":warnings})
            return BusinessResult[ParseDocumentsData](case_id=case_id,case_version=version,
                status="insufficient" if warnings else "ok",summary=f"解析 {len(documents)} 个文件，提取 {len(facts)} 项未确认候选事实。",
                data=ParseDocumentsData(documents=documents,text_segments=segments,extracted_facts=facts,warnings=warnings))
        except VersionConflict as exc:
            return self._error(case_id,exc.actual,"version_conflict",str(exc))
        except (ValidationError,ValueError) as exc:
            return self._error(case_id,version,"invalid_request","材料解析请求不合法。",{"reason":str(exc).splitlines()[0]})
        except CaseNotFound:
            return self._error(case_id,version,"not_found","案件不存在。")

    def _parse(self, record, content, mode, *, warnings=None, deadline_at=None):
        warnings = warnings if warnings is not None else []
        if record.media_type=="text/plain":
            if mode=="always":
                raise ValueError("always OCR is only valid for image/PDF inputs")
            text=content.decode("utf-8-sig")
            return self._segments(record,None,text,"text",[],engine="utf8",engine_version="1")
        if record.media_type=="application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            if mode=="always":
                raise ValueError("always OCR requires an image or PDF; export the Word document to PDF first")
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist())>100*1024*1024:
                    raise ValueError("expanded Word file exceeds the parsing limit")
            document=Document(io.BytesIO(content))
            blocks=[]
            for block in document.element.body:
                if block.tag.endswith("}p"):
                    blocks.append(Paragraph(block,document).text)
                elif block.tag.endswith("}tbl"):
                    blocks.extend("\t".join(cell.text for cell in row.cells) for row in Table(block,document).rows)
            text="\n".join(blocks)
            return self._segments(record,None,text,"text",[],engine="python-docx",engine_version=package_version("python-docx"),
                warnings=["Word 按正文段落与表格原顺序提取；未恢复页眉页脚、浮动文字、嵌套表格、修订或原始分页。"])
        if record.media_type=="application/pdf":
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("encrypted PDF needs an explicitly decrypted input")
            if len(reader.pages)>self.max_pages:
                raise ValueError("PDF exceeds page limit")
            segments=[]
            renderer=None
            try:
                for number,page in enumerate(reader.pages,1):
                    if deadline_at and datetime.now(timezone.utc)>=deadline_at:
                        warnings.append(f"{record.file_id}/第{number}页起: 已达到解析截止时间，剩余页需要重试。")
                        break
                    try:
                        text=""
                        if mode!="always":
                            try:
                                text=page.extract_text() or ""
                            except Exception as exc:
                                if mode=="never":
                                    raise
                                warnings.append(f"{record.file_id}/第{number}页: 文字层读取失败，尝试OCR（{type(exc).__name__}）。")
                        usable=sum(char.isalnum() for char in text)>=3
                        if mode!="always" and usable:
                            page_segments=self._segments(record,number,text,"text",[],engine="pypdf",engine_version=package_version("pypdf"),
                                warnings=["PDF 文字层可能不含手写批注；需要识别批注时显式使用 always OCR，并核对渲染是否包含批注。"])
                        elif mode=="never":
                            warnings.append(f"{record.file_id}/第{number}页: 缺少可用文字层且 OCR 已关闭，需要补充清晰材料。")
                            page_segments=self._segments(record,number,text,"text",[],engine="pypdf",engine_version=package_version("pypdf"))
                        else:
                            import pypdfium2
                            if renderer is None:
                                renderer=pypdfium2.PdfDocument(content)
                            rendered_page=renderer[number-1]
                            try:
                                width,height=rendered_page.get_size()
                                if (not all(math.isfinite(value) and value>0 for value in (width,height))
                                    or math.ceil(width*2)*math.ceil(height*2)>30_000_000):
                                    raise ValueError("PDF rendered page exceeds 30 megapixel limit or has invalid dimensions")
                                bitmap=rendered_page.render(scale=2,draw_annots=True)
                                try:
                                    buffer=io.BytesIO()
                                    bitmap.to_pil().save(buffer,format="PNG")
                                    page_segments=self._ocr_segments(record,number,buffer.getvalue())
                                finally:
                                    bitmap.close()
                            finally:
                                rendered_page.close()
                        if not page_segments:
                            warnings.append(f"{record.file_id}/第{number}页: 未提取到文字，不能当作本页没有材料。")
                        segments.extend(page_segments)
                    except Exception as exc:
                        warnings.append(f"{record.file_id}/第{number}页: {type(exc).__name__}: {str(exc)[:160]}；本页未完成，需要重试。")
            finally:
                if renderer is not None:
                    renderer.close()
            return segments
        if record.media_type in {"image/png","image/jpeg","image/webp","image/tiff"}:
            if mode=="never":
                raise ValueError("image input requires OCR; OCR mode is never")
            from PIL import Image
            with Image.open(io.BytesIO(content)) as image:
                if getattr(image,"n_frames",1)>1:
                    raise ValueError("multi-frame image is not supported; supply a PDF or individual images")
                if image.width*image.height>30_000_000:
                    raise ValueError("image exceeds 30 megapixel limit")
            return self._ocr_segments(record,1,content)
        raise ValueError(f"unsupported media_type: {record.media_type}")

    def _ocr_segments(self,record,page,content):
        if self.ocr is None:
            raise RuntimeError("OCR engine is not configured")
        result=self.ocr.recognize(content)
        lines=result.lines
        text="\n".join(line.text for line in lines)
        warnings=list(result.warnings)+["OCR 结果尚未确认，手写日期、姓名和金额需要核对原图。"]
        return self._segments(record,page,text,"ocr",lines,engine=result.engine,
            engine_version=result.engine_version+":"+result.model_version,warnings=warnings)

    def _segments(self,record,page,text,method,lines,*,engine,engine_version,warnings=None):
        if not text.strip():
            return []
        geometry=json.dumps([line.model_dump(mode="json") for line in lines],sort_keys=True,ensure_ascii=False)
        extraction=hashlib.sha256((engine+":"+engine_version+":"+text+":"+geometry).encode()).hexdigest()
        self.assets.save_extraction(record,extraction,str(page) if page else "text",text)
        if not lines:
            return [ExtractedText(material_ref=MaterialRef(file_id=record.file_id,file_version=record.file_version,page=page,
                span_start=0,span_end=len(text),quote=text,extraction_version=extraction),text=text,extraction_method=method,
                engine=engine,engine_version=engine_version,warnings=warnings or [])]
        result=[]
        start=0
        for line in lines:
            notices=list(warnings or [])
            if line.confidence is None or line.confidence<self.low_confidence:
                notices.append("低分或无识别分数，请人工确认；分数不是正确率。")
            result.append(ExtractedText(material_ref=MaterialRef(file_id=record.file_id,file_version=record.file_version,page=page,
                span_start=start,span_end=start+len(line.text),quote=line.text,extraction_version=extraction,polygon=line.polygon),
                text=line.text,extraction_method=method,confidence=line.confidence,engine=engine,engine_version=engine_version,warnings=notices))
            start+=len(line.text)+1
        return result

    @staticmethod
    def _error(case_id,version,code,message,details=None):
        return BusinessResult[ParseDocumentsData](case_id=case_id,case_version=version,status="error",summary=message,data=None,
            error=BusinessError(code=code,message=message,details=details))
