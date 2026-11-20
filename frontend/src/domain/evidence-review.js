// Presentation-only warnings: a valid synthetic fixture is not an OCR result.
export function evidenceReview(caseData, signature) {
  const result = caseData.demoAnalysis;
  const demo = Boolean(caseData.synthetic === true &&
    caseData.demoScenario === 'full-traffic' &&
    result && result.basis === signature &&
    JSON.stringify(result.files) === JSON.stringify(caseData.files.map((file) => file.id)));
  return {
    demo,
    duplicate: demo
      ? '演示材料中的 800 元票据存在重复扫描页，请核对票据编号、日期及原件，避免重复计入。'
      : '解析后在这里显示疑似重复文件、重复页面或重复票据，并定位到相关材料。当前尚未核验。',
    missing: demo
      ? '演示待核验项：保险保单原件、理赔反馈及是否已有垫付款；请补充材料或说明暂时无法提供的原因。'
      : '核对后在这里列出支撑当前请求仍缺少的材料。可补充上传，也可在案情沟通中说明原因。'
  };
}
