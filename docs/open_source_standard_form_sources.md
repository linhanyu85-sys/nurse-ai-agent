# 开源标准表单来源

本项目当前把护理文书的结构化编辑与 AI 草稿生成，统一映射到“开源标准表单 + 本地护理文书规范”的混合方案。

说明：
- 体温单、危重护理记录、血糖记录等字段层，优先参考开源标准 `openEHR` 的生命体征与血糖 archetype。
- 表单承载结构与可编辑问卷风格，参考 `HL7 FHIR Questionnaire / Structured Data Capture (SDC)` 官方规范。
- 中国临床护理文书的具体栏目顺序、书写提醒与交接班逻辑，仍以用户提供的护理文书规范为本地标准化依据。

## 主要开源来源

### HL7 FHIR / SDC
- FHIR Questionnaire 官方说明
  - https://build.fhir.org/questionnaire.html
- HL7 Structured Data Capture (SDC) Implementation Guide
  - https://build.fhir.org/ig/HL7/sdc/

### openEHR CKM
- Body Temperature 体温
  - https://ckm.openehr.org/ckm/archetypes/1013.1.1790
- Pulse/Heart beat 脉搏/心率
  - https://ckm.openehr.org/ckm/archetypes/1013.1.2131
- Respirations 呼吸
  - https://ckm.openehr.org/ckm/archetypes/1013.1.1982
- Pulse oximetry / Oxygen saturation 血氧饱和度
  - https://ckm.openehr.org/ckm/archetypes/1013.1.2032
- Blood Pressure 血压
  - https://ckm.openehr.org/ckm/archetypes/1013.1.2113
- Blood Glucose 血糖
  - https://ckm.openehr.org/ckm/archetypes/1013.1.2883

## 当前项目内的落地方式

- 后端标准表单定义：
  - `services/document-service/app/services/standard_forms.py`
- 系统模板文本：
  - `services/document-service/app/services/system_templates.py`
- AI 文书适配与结构化字段输出：
  - `services/document-service/app/services/llm_client.py`
- 手机端结构化文书编辑器：
  - `apps/mobile/src/components/DocumentStructuredEditor.tsx`

## 已接入文书类型

- 一般护理记录单
- 体温单
- 手术物品清点记录单
- 病重（病危）患者护理记录单
- 输血护理记录单
- 血糖测量记录单
- 护理日夜交接班报告
