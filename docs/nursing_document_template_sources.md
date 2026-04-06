# 护理文书模板来源与电子化落地说明

本轮模板库的整理原则有两条：

1. 以你提供的《护理文书书写基本规范》内容作为字段、书写顺序和提醒语的主依据。
2. 用公开可查的电子病历/电子护理表单资料校对电子化呈现方式，而不是直接照搬外部 PDF 原表。

## 已落地到系统模板库的文书

- 一般护理记录单
- 体温单电子录入模板
- 手术物品清点记录单
- 病重（病危）患者护理记录单
- 输血护理记录单
- 血糖测量记录单（POCT）
- 护理日夜交接班报告

## 公开参考来源

- 北京市卫生健康委员会：电子病历系统场景化建设动态
  - https://wjw.beijing.gov.cn/xwzx_20031/jcdt/202406/t20240615_3713407.html
- OpenMRS Patient Chart
  - https://github.com/openmrs/openmrs-esm-patient-chart
- openEHR Template Library（护理/观察模板）
  - https://ckm.openehr.org/ckm/templates/1013.26.12
- Western Health Nursing Handover Quick Reference
  - https://www.westernhealth.org.au/EducationandResearch/ClinicalSchoolAndPrograms/Documents/Nursing%20Handover%20Quick%20Reference%20Guide.pdf

## 当前项目里的落地方式

- 所有模板都以 `DocumentTemplate` 的系统模板形式内置。
- AI Agent 触发文书工作流时，会先识别文书类型，再自动匹配相应模板。
- 如果没有命中专用模板，会回退到一般护理记录单。
- 交接班报告除了保留专门的 handover 工作流，也同步内置了文书模板，方便护士按指令生成草稿后再审核。
