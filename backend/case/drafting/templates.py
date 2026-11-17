"""Three selectable drafting profiles with explicit provenance and review state.

Version 1 retains its original pending-review provenance. Version 2 uses the
2024 published forms as field-group references, not as a form replica or law.
"""

from .documents import DocumentTemplate, TemplateRegistry

LISTING_URL="https://lx.wxfy.gov.cn/article/detail/2026/01/id/9147338.shtml"
VERSION="civra-fields-v1"
CURRENT_VERSION="civra-fields-v2"
REFERENCE_URL="https://www.acla.org.cn/upload/media/2024-04-25/8de31284e766dd100a19f1d88d462402.pdf"
REFERENCE_SHA256="f9fc415b2d4e96567eecc4a8864a9407a67554cd2432d18cb2199790388a1ff5"
REFERENCE_PAGES={"traffic_accident":[86,87,88],"private_lending":[3,4,5,6],"sales_contract":[27,28,29,30,31]}
ADDITIONS={
    "traffic_accident":{"accident_details":"事故经过","other_losses":"其他损失及请求（如无，请明确确认）"},
    "private_lending":{
        "loan_roles":"出借人、借款人角色","loan_term":"借款期间及到期日",
        "overdue_details":"逾期或提前到期情况","preservation":"保全情况及申请意愿（尚待核实，不自动申请）",
        "security_details":"抵押、质押、保证等具体情况及范围（不适用时需确认）",
        "collection_costs":"主张的实现债权费用及依据"},
    "sales_contract":{
        "contract_roles":"买方、卖方角色","delivery_agreement":"交付、风险转移、安装、调试及验收约定",
        "quality_agreement":"质量标准、检验及异议期限约定","penalty_agreement":"违约金、定金等约定",
        "loss_calculation":"利息、违约金或损失的计算说明","preservation":"保全情况及申请意愿（尚待核实，不自动申请）",
        "security_details":"抵押、质押、保证等情况及范围（不适用时需确认）",
        "collection_costs":"主张的实现债权费用及依据"},
}
COMMON_FIELDS={
    "claimant_name":"原告姓名或名称",
    "claimant_identity":"原告身份与主体信息",
    "claimant_contact":"原告住所及联系方式",
    "defendant_name":"被告姓名或名称",
    "defendant_identity":"被告身份与主体信息",
    "defendant_contact":"被告住所及联系方式",
    "representative":"诉讼代理信息（无代理人时需明确确认）",
    "court_name":"拟提交法院（需核实管辖）",
    "mediation_preference":"对先行调解的意愿",
}
PROFILES=[
    ("traffic_accident","机动车交通事故责任纠纷",
        "http://img5.chinacourt.cn/mup/uploadfile/2026/01/09/14/53cab8af28e3cde6b43e6d7c7b6ae26e.docx",
        {"accident_date":"事故日期","accident_location":"事故地点及经过",
         "vehicles":"涉事车辆及人员信息","liability_finding":"责任认定记载及认定书信息",
         "insurance":"保险投保情况","loss_details":"损失项目、金额及计算说明"}),
    ("private_lending","民间借贷纠纷",
        "http://img1.chinacourt.cn/mup/uploadfile/2026/01/09/14/d3d229ca25f11bacdd77bdf2794dfa35.docx",
        {"loan_agreement":"借贷约定及合同/借据情况","loan_principal":"主张本金",
         "loan_delivery":"款项交付时间、方式及凭证","repayment_agreement":"还款期限及约定",
         "repayment_history":"已还本金、利息及明细","interest_agreement":"利息、违约金约定及计算请求",
         "guarantee":"担保情况（无担保时需明确确认）","jurisdiction_agreement":"仲裁或管辖约定"}),
    ("sales_contract","买卖合同纠纷",
        "http://img3.chinacourt.cn/mup/uploadfile/2026/01/09/14/65ad67ae4d675c1d1fea6b5198dbc9a1.docx",
        {"contract_details":"买卖合同名称、签订情况及当事人角色","subject_matter":"标的物、规格及数量",
         "price_agreement":"价款和付款约定","delivery_history":"交付、收货及验收情况",
         "payment_history":"已付款、欠款及明细","breach_details":"违约、质量或履行争议",
         "performance_demands":"催告或协商情况","jurisdiction_agreement":"仲裁或管辖约定"}),
]


def load_builtin_templates() -> TemplateRegistry:
    registry=TemplateRegistry()
    for dispute_type,label,url,fields in PROFILES:
        registry.register(DocumentTemplate(template_id="cn_complaint_"+dispute_type,template_version=VERSION,
            title="民事起诉状填写草稿（"+label+"）",origin_kind="official_reference_pending_form_review",
            source_refs=[],dispute_types=[dispute_type],fields={**COMMON_FIELDS,**fields},
            source_url=url,source_version="source-listing-2026-01-09",
            notes=["本文件为 CivRA 填写草稿；尚未逐项比对法院原表字段及排版，不能作为法院原表副本。",
                "法院公开模板目录："+LISTING_URL,"对应原表下载入口："+url,
                "未知信息保留待补充；填入正式表格前核对最新原表、全部要素和适用情况。"] ))
    for dispute_type,label,_,fields in PROFILES:
        common={key:value for key,value in COMMON_FIELDS.items() if key!="court_name"}
        common.update(additional_parties="第三人及其他当事人（如无，请明确确认）",
            service_address="送达地址、收件人及联系方式",
            electronic_service="是否同意电子送达及具体渠道（需本人明确选择）")
        facts={**fields,**ADDITIONS[dispute_type]}
        if dispute_type=="traffic_accident":
            facts["accident_location"]="事故地点"
        closing={"court_name":"拟提交法院（需核实管辖）","signature":"具状人签名或盖章（本人核对后完成）",
            "signature_date":"签署日期（本人核对后填写）"}
        registry.register(DocumentTemplate(template_id="cn_complaint_"+dispute_type,template_version=CURRENT_VERSION,
            title="民事起诉状填写草稿（"+label+"）",origin_kind="official_reference_field_groups_reviewed",
            source_refs=[],dispute_types=[dispute_type],fields={**common,**facts,**closing},
            field_sections={**{key:"当事人与送达信息" for key in common},**{key:"事实与材料信息" for key in facts},
                **{key:"致送与签署" for key in closing}},
            source_url=REFERENCE_URL,source_version="published-2024-04-25-sha256-"+REFERENCE_SHA256[:16],
            review_metadata=dict(review_date="2026-11-09",reference_sha256=REFERENCE_SHA256,
                pdf_pages_1_based=REFERENCE_PAGES[dispute_type],review_scope="field_groups",
                official_form_replica=False,current_court_acceptance_verified=False),
            notes=["本稿按2024年公开要素式起诉状整理字段组；不是原表复刻，也未证明适用于当前受理法院。",
                "当事人身份、代理权限、担保等采用汇总字段，正式填写时仍需按原表拆分和逐项校对。",
                "诉讼请求仅采用本案用户确认内容；其他损失、费用、担保责任不因模板存在对应栏目而自动提出。",
                "参考文本：中华全国律师协会公开附件，PDF第"+"、".join(map(str,REFERENCE_PAGES[dispute_type]))+"页。",
                "提交前应核对受理法院现行表格、所需材料及适用情况。"] ))
    return registry
