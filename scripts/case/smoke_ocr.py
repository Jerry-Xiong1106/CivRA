"""Actual local PaddleOCR smoke test on a generated printed Chinese/numeric image."""

import argparse
import io
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from backend.case.ocr import OcrEngineRegistry


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=ROOT/"data/config/member3_ocr.json")
    parser.add_argument("--image",type=Path)
    parser.add_argument("--font",type=Path,default=Path("C:/Windows/Fonts/msyh.ttc"))
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    if args.image:
        content=args.image.read_bytes()
    else:
        from PIL import Image,ImageDraw,ImageFont
        image=Image.new("RGB",(1250,450),"white")
        draw=ImageDraw.Draw(image)
        font=ImageFont.truetype(str(args.font),44)
        for index,text in enumerate(["合成开发样例", "事故日期：2026-05-12", "维修金额：3000.00元"]):
            draw.text((45,35+index*120),text,font=font,fill="black")
        stream=io.BytesIO()
        image.save(stream,format="PNG")
        content=stream.getvalue()
    (args.output_dir/"input.png").write_bytes(content)
    config=json.loads(args.config.read_text(encoding="utf-8"))
    engine=OcrEngineRegistry().create(config["engine"],config["options"])
    started=time.monotonic()
    result=engine.recognize(content)
    payload={"elapsed_ms":round((time.monotonic()-started)*1000),"result":result.model_dump(mode="json"),
        "scope":"Single printed synthetic image smoke test; not a handwriting or overall accuracy evaluation."}
    (args.output_dir/"result.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
