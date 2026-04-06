import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from "react-native";

import type { DocumentDraft, DocumentStructuredFields, DraftEditableBlock } from "../types";
import { colors } from "../theme";
import {
  buildSheetRows,
  formatArchiveHint,
  getDraftStandardForm,
  getEditableBlocks,
  getSectionMeta,
  renderStructuredDraftText,
  updateEditableBlockValue,
} from "../utils/documentDraft";
import { getDocumentTypeLabel } from "../utils/displayText";
import { ActionButton, StatusPill, SurfaceCard } from "./ui";

type Props = {
  draft: DocumentDraft;
  draftText: string;
  structuredFields: DocumentStructuredFields;
  busy?: boolean;
  presentation?: "auto" | "inline";
  onDraftTextChange: (value: string) => void;
  onStructuredFieldsChange: (value: DocumentStructuredFields) => void;
  onCancel: () => void;
  onSave: () => void;
};

type EditorMode = "form" | "sheet" | "word" | "preview";

const WORD_OBSERVATION_TEMPLATE = [
  "护理观察：",
  "1. 主要症状与体征：",
  "2. 本班关键监测：",
  "3. 已执行护理措施：",
  "4. 效果评价：",
  "5. 下一班继续观察重点：",
].join("\n");

const WORD_TABLE_TEMPLATE = [
  "监测项目 | 时间 | 数值/表现 | 已做处理 | 复核结果",
  "--- | --- | --- | --- | ---",
  "生命体征 |  |  |  |  ",
  "异常观察 |  |  |  |  ",
  "护理措施 |  |  |  |  ",
  "医生沟通 |  |  |  |  ",
].join("\n");

function normalizeText(value: string) {
  return String(value || "").replace(/\r/g, "");
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function textToEditorHtml(value: string) {
  const normalized = normalizeText(value);
  if (!normalized) {
    return "";
  }
  return escapeHtml(normalized).replace(/\n/g, "<br />");
}

function normalizeEditorText(value: string) {
  return normalizeText(value).replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trimEnd();
}

function appendBlock(source: string, block: string) {
  const base = normalizeText(source).trimEnd();
  if (!base) {
    return block;
  }
  return `${base}\n\n${block}`;
}

function EditorMetric(props: { label: string; value: string; tone?: "info" | "success" | "warning" }) {
  return (
    <View style={styles.metricCard}>
      <Text style={styles.metricValue}>{props.value}</Text>
      <Text style={styles.metricLabel}>{props.label}</Text>
      {props.tone ? <StatusPill text={props.tone === "warning" ? "待补" : props.tone === "success" ? "可提交" : "已同步"} tone={props.tone} /> : null}
    </View>
  );
}

function ModeChip(props: { active: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable style={[styles.modeChip, props.active && styles.modeChipActive]} onPress={props.onPress}>
      <Text style={[styles.modeChipText, props.active && styles.modeChipTextActive]}>{props.label}</Text>
    </Pressable>
  );
}

function ToolbarChip(props: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.toolbarChip} onPress={props.onPress}>
      <Text style={styles.toolbarChipText}>{props.label}</Text>
    </Pressable>
  );
}

function FilterChip(props: { active: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable style={[styles.filterChip, props.active && styles.filterChipActive]} onPress={props.onPress}>
      <Text style={[styles.filterChipText, props.active && styles.filterChipTextActive]}>{props.label}</Text>
    </Pressable>
  );
}

function FieldInput(props: { field: DraftEditableBlock; onChange: (value: string) => void }) {
  const multiline = props.field.input_type === "textarea";
  return (
    <View style={styles.fieldCard}>
      <View style={styles.fieldHead}>
        <View style={styles.fieldHeadText}>
          <Text style={styles.fieldLabel}>{props.field.label}</Text>
          <Text style={styles.fieldSectionLabel}>{props.field.section}</Text>
        </View>
        <View style={styles.fieldStatusRow}>
          <StatusPill text={props.field.required ? "必填" : "可补"} tone={props.field.required ? "warning" : "info"} />
          <StatusPill text={props.field.status === "missing" ? "待补" : "已填"} tone={props.field.status === "missing" ? "warning" : "success"} />
        </View>
      </View>
      <TextInput
        value={props.field.value}
        onChangeText={props.onChange}
        placeholder={props.field.placeholder || "请输入内容"}
        placeholderTextColor={colors.subText}
        multiline={multiline}
        style={[styles.fieldInput, multiline && styles.fieldInputMultiline]}
        textAlignVertical={multiline ? "top" : "center"}
      />
    </View>
  );
}

function WordCanvas(props: { value: string; onChange: (value: string) => void; compact?: boolean }) {
  const webRef = useRef<any>(null);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (Platform.OS !== "web" || !webRef.current || focused) {
      return;
    }
    const current = normalizeEditorText(String(webRef.current.innerText || ""));
    const next = normalizeText(props.value);
    if (current !== next) {
      webRef.current.innerHTML = textToEditorHtml(next);
    }
  }, [focused, props.value]);

  if (Platform.OS === "web") {
    return React.createElement("div", {
      ref: webRef,
      contentEditable: true,
      suppressContentEditableWarning: true,
      onFocus: () => setFocused(true),
      onBlur: () => {
        setFocused(false);
        props.onChange(normalizeEditorText(String(webRef.current?.innerText || "")));
      },
      onInput: () => {
        props.onChange(normalizeEditorText(String(webRef.current?.innerText || "")));
      },
      style: {
        minHeight: props.compact ? 280 : 420,
        borderRadius: 18,
        border: "1px solid #d7e0e2",
        backgroundColor: "#ffffff",
        color: colors.text,
        padding: "16px 18px",
        lineHeight: "1.7",
        fontSize: "14px",
        whiteSpace: "pre-wrap",
        overflowY: "auto",
        outline: "none",
        boxShadow: "inset 0 1px 2px rgba(15, 23, 42, 0.04)",
      },
      dangerouslySetInnerHTML: {
        __html: textToEditorHtml(props.value),
      },
    });
  }

  return (
    <TextInput
      value={props.value}
      onChangeText={props.onChange}
      placeholder="继续完善正文"
      placeholderTextColor={colors.subText}
      multiline
      textAlignVertical="top"
      style={[styles.wordCanvasNative, props.compact && styles.wordCanvasNativeCompact]}
    />
  );
}

export function DocumentStructuredEditor(props: Props) {
  const { width } = useWindowDimensions();
  const containerRef = useRef<any>(null);
  const [mode, setMode] = useState<EditorMode>("word");
  const [keyword, setKeyword] = useState("");
  const [focusSection, setFocusSection] = useState("全部");
  const [showOnlyMissing, setShowOnlyMissing] = useState(false);
  const wideLayout = width >= 1680;
  const compactLayout = width < 1024 || Platform.OS !== "web";
  const renderInline = props.presentation === "inline" || Platform.OS === "web";

  useEffect(() => {
    const nextMissingCount = Number(props.structuredFields.field_summary?.missing || 0);
    setMode(nextMissingCount > 0 ? "form" : "word");
    setKeyword("");
    setFocusSection("全部");
    setShowOnlyMissing(false);
  }, [props.draft.id]);

  useEffect(() => {
    if (Platform.OS !== "web") {
      return;
    }
    const timer = setTimeout(() => {
      if (typeof containerRef.current?.scrollIntoView === "function") {
        containerRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 40);
    return () => clearTimeout(timer);
  }, [props.draft.id]);

  const draft = useMemo(
    () => ({
      ...props.draft,
      structured_fields: props.structuredFields,
    }),
    [props.draft, props.structuredFields]
  );

  const sections = useMemo(() => getSectionMeta(draft), [draft]);
  const editableBlocks = useMemo(() => getEditableBlocks(draft), [draft]);
  const sheetRows = useMemo(() => buildSheetRows(draft), [draft]);
  const standardForm = useMemo(() => getDraftStandardForm(draft), [draft]);
  const missingFields = useMemo(() => editableBlocks.filter((item) => item.required && item.status === "missing"), [editableBlocks]);
  const missingCount = Number(props.structuredFields.field_summary?.missing || missingFields.length);
  const filledCount = Number(props.structuredFields.field_summary?.filled || editableBlocks.filter((item) => item.value.trim()).length);
  const totalCount = Number(props.structuredFields.field_summary?.total || editableBlocks.length);
  const previewText = useMemo(() => {
    const generated = renderStructuredDraftText(props.draft.document_type, props.structuredFields);
    const normalizedDraftText = normalizeText(props.draftText).trim();
    if (/\{\{\s*[^{}]+\s*\}\}/.test(normalizedDraftText) || /^\{[^{}]+\}$/.test(normalizedDraftText)) {
      return generated;
    }
    return normalizedDraftText || generated;
  }, [props.draft.document_type, props.draftText, props.structuredFields]);

  const groupedBlocks = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    const map = new Map<string, DraftEditableBlock[]>();
    editableBlocks.forEach((block) => {
      const haystack = `${block.section} ${block.label} ${block.value}`.toLowerCase();
      if (needle && !haystack.includes(needle)) {
        return;
      }
      if (showOnlyMissing && block.status !== "missing") {
        return;
      }
      if (focusSection !== "全部" && block.section !== focusSection) {
        return;
      }
      map.set(block.section, [...(map.get(block.section) || []), block]);
    });
    return map;
  }, [editableBlocks, focusSection, keyword, showOnlyMissing]);

  const filteredSheetRows = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return sheetRows.filter((row) => {
      if (focusSection !== "全部" && row.section !== focusSection) {
        return false;
      }
      if (showOnlyMissing && row.status !== "missing") {
        return false;
      }
      if (!needle) {
        return true;
      }
      return `${row.section} ${row.label} ${row.value}`.toLowerCase().includes(needle);
    });
  }, [focusSection, keyword, sheetRows, showOnlyMissing]);

  const sectionOptions = useMemo(() => ["全部", ...sections.map((item) => item.title)], [sections]);

  const syncDraftFromFields = (nextStructuredFields: DocumentStructuredFields) => {
    props.onDraftTextChange(renderStructuredDraftText(props.draft.document_type, nextStructuredFields));
  };

  const updateField = (key: string, value: string) => {
    const nextStructuredFields = updateEditableBlockValue(props.structuredFields, key, value);
    props.onStructuredFieldsChange(nextStructuredFields);
    syncDraftFromFields(nextStructuredFields);
  };

  const applyToFields = (matcher: (field: DraftEditableBlock) => boolean, nextValue: (field: DraftEditableBlock) => string) => {
    let nextStructuredFields = props.structuredFields;
    let changed = false;
    editableBlocks.forEach((field) => {
      if (!matcher(field)) {
        return;
      }
      const value = nextValue(field);
      if (value === field.value) {
        return;
      }
      nextStructuredFields = updateEditableBlockValue(nextStructuredFields, field.key, value);
      changed = true;
    });
    if (!changed) {
      return;
    }
    props.onStructuredFieldsChange(nextStructuredFields);
    syncDraftFromFields(nextStructuredFields);
  };

  const fillCurrentTime = () => {
    const now = new Date();
    const dateText = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const dateTimeText = `${dateText} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    applyToFields(
      (field) => !field.value.trim() && /(date|time|日期|时间)/i.test(field.key),
      (field) => (/date/i.test(field.key) && !/time/i.test(field.key) ? dateText : dateTimeText)
    );
  };

  const rebuildDraftText = () => {
    syncDraftFromFields(props.structuredFields);
  };

  const appendWordBlock = (block: string) => {
    props.onDraftTextChange(appendBlock(props.draftText, block));
  };

  const runWebEditorCommand = (command: string, value?: string) => {
    if (Platform.OS !== "web") {
      return false;
    }
    const webDoc = (globalThis as { document?: Document }).document;
    if (!webDoc || typeof webDoc.execCommand !== "function") {
      return false;
    }
    try {
      webDoc.execCommand(command, false, value);
      return true;
    } catch {
      return false;
    }
  };

  const applyWordCommand = (kind: "heading" | "bold" | "bullet" | "observation" | "table") => {
    if (kind === "observation") {
      appendWordBlock(WORD_OBSERVATION_TEMPLATE);
      return;
    }
    if (kind === "table") {
      appendWordBlock(WORD_TABLE_TEMPLATE);
      return;
    }

    if (Platform.OS === "web") {
      const applied =
        kind === "heading"
          ? runWebEditorCommand("formatBlock", "<h3>")
          : kind === "bold"
          ? runWebEditorCommand("bold")
          : runWebEditorCommand("insertUnorderedList");
      if (applied) {
        setTimeout(() => {
          props.onDraftTextChange(
            normalizeEditorText(String((globalThis as any)?.document?.activeElement?.innerText || props.draftText))
          );
        }, 0);
        return;
      }
    }

    if (kind === "heading") {
      appendWordBlock("【护理重点】");
    } else if (kind === "bold") {
      appendWordBlock("重点：");
    } else {
      appendWordBlock("- ");
    }
  };

  const sourceRefsText = standardForm?.source_refs?.length ? standardForm.source_refs.join(" / ") : "";
  const visibleFormSections = useMemo(() => {
    const preferred = sections
      .map((section) => ({ title: section.title, fields: groupedBlocks.get(section.title) || [] }))
      .filter((section) => section.fields.length);
    if (preferred.length) {
      return preferred;
    }
    return Array.from(groupedBlocks.entries())
      .map(([title, fields]) => ({
        title: title || "未分组字段",
        fields,
      }))
      .filter((section) => section.fields.length);
  }, [groupedBlocks, sections]);
  const hasGroupedBlocks = visibleFormSections.length > 0;
  const compactSheetRows = filteredSheetRows.length ? filteredSheetRows : sheetRows;

  const compactEditorBody = (
    <>
      {false ? <SurfaceCard style={styles.compactSummaryPanel}>
        <Text style={styles.previewTitle}>草稿概览</Text>
        <Text style={styles.compactSummaryText}>
          可编辑字段 {totalCount} 项，已填写 {filledCount} 项，待补 {missingCount} 项。保存后仍留在草稿区，审核通过后才归档。
        </Text>
        {sourceRefsText ? <Text style={styles.asideMeta}>模板来源：{sourceRefsText}</Text> : null}
      </SurfaceCard> : null}

      <View style={styles.compactToolbarActions}>
        <ToolbarChip label="补当前时间" onPress={fillCurrentTime} />
        <ToolbarChip label="字段回填正文" onPress={rebuildDraftText} />
        <ToolbarChip label="观察段落" onPress={() => applyWordCommand("observation")} />
        <ToolbarChip label="监测表" onPress={() => applyWordCommand("table")} />
      </View>

      <View style={[styles.editorPanel, styles.editorPanelCompact]}>
        <Text style={styles.panelTitle}>结构化字段</Text>
        <View style={styles.fieldList}>
          {visibleFormSections.map((section) => {
            const fields = section.fields;
            return (
              <View key={section.title} style={styles.sectionBlock}>
                <View style={styles.sectionHead}>
                  <Text style={styles.sectionTitle}>{section.title}</Text>
                  <Text style={styles.sectionMeta}>
                    共 {fields.length} 项 · 待补 {fields.filter((field) => field.required && field.status === "missing").length}
                  </Text>
                </View>
                <View style={styles.fieldList}>
                  {fields.map((field) => (
                    <FieldInput key={field.key} field={field} onChange={(value) => updateField(field.key, value)} />
                  ))}
                </View>
              </View>
            );
          })}
          {!hasGroupedBlocks ? <Text style={styles.emptyText}>当前没有命中的可编辑字段，请先回到草稿重新加载。</Text> : null}
        </View>
      </View>

      <View style={[styles.editorPanel, styles.editorPanelCompact]}>
        <Text style={styles.panelTitle}>Excel 表格录入</Text>
        <Text style={styles.panelHint}>适合补时间点、生命体征、出入量、签名等表格字段。</Text>
        <View style={styles.fieldList}>
          {compactSheetRows.map((row) => (
            <View key={row.key} style={styles.fieldCard}>
              <View style={styles.fieldHead}>
                <View style={styles.fieldHeadText}>
                  <Text style={styles.fieldLabel}>
                    {row.label}
                    {row.required ? " *" : ""}
                  </Text>
                  <Text style={styles.fieldSectionLabel}>{row.section}</Text>
                </View>
                <StatusPill text={row.status === "missing" ? "待补" : "已填"} tone={row.status === "missing" ? "warning" : "success"} />
              </View>
              <TextInput
                value={row.value}
                onChangeText={(value) => updateField(row.key, value)}
                placeholder={row.placeholder || "填写内容"}
                placeholderTextColor={colors.subText}
                multiline={row.input_type === "textarea"}
                style={[styles.fieldInput, row.input_type === "textarea" && styles.fieldInputMultiline]}
                textAlignVertical={row.input_type === "textarea" ? "top" : "center"}
              />
            </View>
          ))}
          {!compactSheetRows.length ? <Text style={styles.emptyText}>当前模板没有可录入的表格字段。</Text> : null}
        </View>
      </View>

      <View style={[styles.editorPanel, styles.editorPanelCompact]}>
        <Text style={styles.panelTitle}>Word 正文</Text>
        <Text style={styles.panelHint}>适合补全病情观察、护理措施、效果评价和交接班文字。</Text>
        <WordCanvas value={props.draftText} onChange={props.onDraftTextChange} compact />
      </View>

      <View style={[styles.editorPanel, styles.editorPanelCompact]}>
        <Text style={styles.panelTitle}>归档预览</Text>
        <Text style={styles.panelHint}>这里显示审核后会归档到患者病例下的最终内容。</Text>
        <SurfaceCard style={styles.previewCard}>
          <Text style={styles.previewTitle}>当前正文</Text>
          <Text style={styles.previewText}>{previewText || "当前还没有可预览内容，请先补字段或完善正文。"}</Text>
        </SurfaceCard>
        <SurfaceCard style={styles.checklistCard}>
          <Text style={styles.previewTitle}>提交前检查</Text>
          <Text style={styles.checklistItem}>1. 核对患者标识、床号、文书类型和时间点。</Text>
          <Text style={styles.checklistItem}>2. 核对异常观察、护理措施和医生沟通记录。</Text>
          <Text style={styles.checklistItem}>3. 待补字段未清空时，继续保留在草稿区，不直接归档。</Text>
        </SurfaceCard>
      </View>
    </>
  );

  const compactEditorCard = (
    <View ref={containerRef} style={[styles.modalCard, styles.compactCard, styles.inlineCardCompact, renderInline && styles.compactInlineCard]}>
      <View style={styles.compactHeader}>
        <View style={styles.modalHeaderText}>
          <Text style={styles.modalTitle}>{getDocumentTypeLabel(props.draft.document_type)}</Text>
          <Text style={styles.modalSubtitle}>{formatArchiveHint(props.draft)}</Text>
          <Text style={styles.modalHint}>手机端改成了单列编辑流，直接在这里填写字段、改正文、录表格并保存草稿。</Text>
          {standardForm ? <Text style={styles.modalMeta}>标准模板：{standardForm.name}</Text> : null}
        </View>
        <View style={styles.modalHeaderAsideCompact}>
          <StatusPill text={props.draft.status === "reviewed" ? "待归档" : "草稿编辑"} tone={props.draft.status === "reviewed" ? "info" : "warning"} />
        </View>
      </View>

      <ScrollView
        style={styles.compactScroll}
        contentContainerStyle={styles.compactScrollContent}
        nestedScrollEnabled
        keyboardShouldPersistTaps="handled"
      >
        {compactEditorBody}
      </ScrollView>

      <View style={styles.footerCompact}>
        <ActionButton label="取消" onPress={props.onCancel} disabled={props.busy} variant="secondary" style={styles.footerButtonCompact} />
        <ActionButton label="保存修改" onPress={props.onSave} disabled={props.busy} style={styles.footerButtonCompact} />
      </View>
    </View>
  );

  const compactInlineEditor = (
    <View ref={containerRef} style={styles.compactInlineStack}>
      <SurfaceCard style={styles.compactInlineHeader}>
        <View style={styles.compactHeader}>
          <View style={styles.modalHeaderText}>
            <Text style={styles.modalTitle}>{getDocumentTypeLabel(props.draft.document_type)}</Text>
            <Text style={styles.modalSubtitle}>{formatArchiveHint(props.draft)}</Text>
            <Text style={styles.modalHint}>手机端改为整页单列编辑，直接在这里补字段、改正文、录表格并保存草稿。</Text>
            {standardForm ? <Text style={styles.modalMeta}>标准模板：{standardForm.name}</Text> : null}
          </View>
          <View style={styles.modalHeaderAsideCompact}>
            <StatusPill text={props.draft.status === "reviewed" ? "待归档" : "草稿编辑"} tone={props.draft.status === "reviewed" ? "info" : "warning"} />
          </View>
        </View>
      </SurfaceCard>

      {compactEditorBody}

      <View style={styles.footerCompact}>
        <ActionButton label="返回上一页" onPress={props.onCancel} disabled={props.busy} variant="secondary" style={styles.footerButtonCompact} />
        <ActionButton label="保存到草稿" onPress={props.onSave} disabled={props.busy} style={styles.footerButtonCompact} />
      </View>
    </View>
  );

  const editorCard = (
    <View
      ref={containerRef}
      style={[
        styles.modalCard,
        wideLayout ? styles.modalCardWide : styles.modalCardNarrow,
        Platform.OS === "web" ? styles.inlineCard : null,
      ]}
    >
          <View style={styles.modalHeader}>
            <View style={styles.modalHeaderText}>
              <Text style={styles.modalTitle}>{getDocumentTypeLabel(props.draft.document_type)}</Text>
              <Text style={styles.modalSubtitle}>{formatArchiveHint(props.draft)}</Text>
              <Text style={styles.modalHint}>
                编辑工作台已经拆成 Word 正文、Excel 样表和结构化字段三种视图，保存后会继续留在草稿区，审核提交后才会自动归档到患者病例。
              </Text>
              {standardForm ? (
                <Text style={styles.modalMeta}>
                  标准模板：{standardForm.name}
                  {standardForm.standard_family ? ` · ${standardForm.standard_family}` : ""}
                </Text>
              ) : null}
              {sourceRefsText ? <Text style={styles.modalMeta}>模板来源：{sourceRefsText}</Text> : null}
            </View>

            <View style={styles.modalHeaderAside}>
              <StatusPill text={props.draft.status === "reviewed" ? "待归档" : "草稿编辑"} tone={props.draft.status === "reviewed" ? "info" : "warning"} />
              <Pressable style={styles.closeButton} onPress={props.onCancel}>
                <Text style={styles.closeButtonText}>关闭</Text>
              </Pressable>
            </View>
          </View>

          <View style={styles.metricRow}>
            <EditorMetric label="可编辑字段" value={String(totalCount)} />
            <EditorMetric label="已填写字段" value={String(filledCount)} tone="success" />
            <EditorMetric label="待补字段" value={String(missingCount)} tone={missingCount > 0 ? "warning" : "success"} />
          </View>

          <View style={styles.modeRow}>
            <ModeChip active={mode === "word"} label="Word 工作区" onPress={() => setMode("word")} />
            <ModeChip active={mode === "sheet"} label="Excel 工作区" onPress={() => setMode("sheet")} />
            <ModeChip active={mode === "form"} label="结构化字段" onPress={() => setMode("form")} />
            <ModeChip active={mode === "preview"} label="归档预览" onPress={() => setMode("preview")} />
          </View>

          <View style={styles.workspaceBody}>
            <View style={styles.workspaceMain}>
              <View style={styles.toolbarRow}>
                <TextInput
                  value={keyword}
                  onChangeText={setKeyword}
                  placeholder="搜索字段、正文或表格内容"
                  placeholderTextColor={colors.subText}
                  style={styles.searchInput}
                />
                <ToolbarChip label="补当前时间" onPress={fillCurrentTime} />
                <ToolbarChip label="字段回填正文" onPress={rebuildDraftText} />
                {mode === "word" ? (
                  <>
                    <ToolbarChip label="标题" onPress={() => applyWordCommand("heading")} />
                    <ToolbarChip label="加粗" onPress={() => applyWordCommand("bold")} />
                    <ToolbarChip label="列表" onPress={() => applyWordCommand("bullet")} />
                    <ToolbarChip label="观察段落" onPress={() => applyWordCommand("observation")} />
                    <ToolbarChip label="监测表" onPress={() => applyWordCommand("table")} />
                  </>
                ) : null}
              </View>

              {(mode === "form" || mode === "sheet") && (
                <View style={styles.filterWrap}>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterScroll}>
                    {sectionOptions.map((section) => (
                      <FilterChip key={section} active={focusSection === section} label={section} onPress={() => setFocusSection(section)} />
                    ))}
                    <FilterChip
                      active={showOnlyMissing}
                      label={showOnlyMissing ? "查看全部" : "仅看待补"}
                      onPress={() => setShowOnlyMissing((value) => !value)}
                    />
                  </ScrollView>
                </View>
              )}

              {missingFields.length ? (
                <View style={styles.warningPanel}>
                  <Text style={styles.warningTitle}>提交前重点核对</Text>
                  <View style={styles.warningTags}>
                    {missingFields.slice(0, 10).map((field) => (
                      <View key={field.key} style={styles.warningTag}>
                        <Text style={styles.warningTagText}>
                          {field.section} · {field.label}
                        </Text>
                      </View>
                    ))}
                  </View>
                </View>
              ) : null}

              {mode === "word" ? (
                <View style={styles.editorPanel}>
                  <Text style={styles.panelTitle}>正文排版</Text>
                  <Text style={styles.panelHint}>
                    这里按 Word 的思路改正文结构；结构化字段的变化可以一键回填到正文，表格录入区则更适合补生命体征、时间点和签名等规范字段。
                  </Text>
                  <WordCanvas value={props.draftText} onChange={props.onDraftTextChange} />
                </View>
              ) : null}

              {mode === "sheet" ? (
                <View style={styles.editorPanel}>
                  <Text style={styles.panelTitle}>表格录入</Text>
                  <Text style={styles.panelHint}>这里按 Excel 的思路逐格补录，适合时间点、生命体征、出入量、签名和交班要点等半结构化字段。</Text>
                  <ScrollView style={styles.sheetScroll} nestedScrollEnabled>
                    <View style={styles.sheetHeader}>
                      <Text style={[styles.sheetHeaderText, styles.sheetIndexCol]}>序号</Text>
                      <Text style={[styles.sheetHeaderText, styles.sheetSectionCol]}>栏位</Text>
                      <Text style={[styles.sheetHeaderText, styles.sheetFieldCol]}>字段</Text>
                      <Text style={[styles.sheetHeaderText, styles.sheetValueCol]}>录入内容</Text>
                    </View>
                    {filteredSheetRows.map((row, index) => (
                      <View key={row.key} style={[styles.sheetRow, index % 2 === 1 && styles.sheetRowAlt]}>
                        <Text style={[styles.sheetIndexText, styles.sheetIndexCol]}>{index + 1}</Text>
                        <Text style={[styles.sheetCellText, styles.sheetSectionCol]}>{row.section}</Text>
                        <Text style={[styles.sheetCellText, styles.sheetFieldCol]}>
                          {row.label}
                          {row.required ? " *" : ""}
                        </Text>
                        <TextInput
                          value={row.value}
                          onChangeText={(value) => updateField(row.key, value)}
                          placeholder={row.placeholder || "填写内容"}
                          placeholderTextColor={colors.subText}
                          multiline={row.input_type === "textarea"}
                          style={[styles.sheetInput, styles.sheetValueCol, row.input_type === "textarea" && styles.sheetInputMultiline]}
                          textAlignVertical={row.input_type === "textarea" ? "top" : "center"}
                        />
                      </View>
                    ))}
                    {!filteredSheetRows.length ? <Text style={styles.emptyText}>当前筛选条件下没有命中的表格字段。</Text> : null}
                  </ScrollView>
                </View>
              ) : null}

              {mode === "form" ? (
                <View style={styles.editorPanel}>
                  <Text style={styles.panelTitle}>结构化字段</Text>
                  <ScrollView style={styles.formScroll} contentContainerStyle={styles.formScrollContent} nestedScrollEnabled>
                    {visibleFormSections.map((section) => {
                      const fields = section.fields;
                      return (
                        <View key={section.title} style={styles.sectionBlock}>
                          <View style={styles.sectionHead}>
                            <Text style={styles.sectionTitle}>{section.title}</Text>
                            <Text style={styles.sectionMeta}>
                              共 {fields.length} 项 · 待补 {fields.filter((field) => field.required && field.status === "missing").length}
                            </Text>
                          </View>
                          <View style={styles.fieldList}>
                            {fields.map((field) => (
                              <FieldInput key={field.key} field={field} onChange={(value) => updateField(field.key, value)} />
                            ))}
                          </View>
                        </View>
                      );
                    })}
                    {!Array.from(groupedBlocks.values()).some((items) => items.length) ? (
                      <Text style={styles.emptyText}>当前筛选条件下没有命中的可编辑字段。</Text>
                    ) : null}
                  </ScrollView>
                </View>
              ) : null}

              {mode === "preview" ? (
                <View style={styles.editorPanel}>
                  <Text style={styles.panelTitle}>归档预览</Text>
                  <ScrollView style={styles.previewScroll} contentContainerStyle={styles.previewScrollContent}>
                    <SurfaceCard style={styles.previewCard}>
                      <Text style={styles.previewTitle}>标准化预览</Text>
                      <Text style={styles.previewText}>{previewText || "当前还没有可预览内容，请先补录字段或完善正文。"}</Text>
                    </SurfaceCard>
                    <SurfaceCard style={styles.checklistCard}>
                      <Text style={styles.previewTitle}>提交前检查清单</Text>
                      <Text style={styles.checklistItem}>1. 患者标识、床号、文书类型、时间点是否一致。</Text>
                      <Text style={styles.checklistItem}>2. 生命体征、出入量、异常观察和护理措施是否前后一致。</Text>
                      <Text style={styles.checklistItem}>3. 需要联系医生的阈值、已沟通内容、交接班重点是否已留痕。</Text>
                      <Text style={styles.checklistItem}>4. 待补字段未清空时，建议继续保留在草稿区，不要直接归档。</Text>
                    </SurfaceCard>
                  </ScrollView>
                </View>
              ) : null}
            </View>

            {wideLayout ? (
              <View style={styles.workspaceAside}>
                <SurfaceCard style={styles.asideCard}>
                  <Text style={styles.asideTitle}>模板说明</Text>
                  <Text style={styles.asideText}>
                    {standardForm?.description || "当前文书已按标准化模板生成，字段缺失时会保留待补，不会自动编造临床数据。"}
                  </Text>
                  {sourceRefsText ? <Text style={styles.asideMeta}>来源：{sourceRefsText}</Text> : null}
                </SurfaceCard>
                <SurfaceCard style={styles.asideCard}>
                  <Text style={styles.asideTitle}>当前正文摘要</Text>
                  <Text style={styles.asideText} numberOfLines={18}>
                    {previewText || "尚未生成正文内容。"}
                  </Text>
                </SurfaceCard>
              </View>
            ) : null}
          </View>

          <View style={styles.footer}>
            <ActionButton label="取消" onPress={props.onCancel} disabled={props.busy} variant="secondary" style={styles.footerButton} />
            <ActionButton label="保存修改" onPress={props.onSave} disabled={props.busy} style={styles.footerButton} />
          </View>
    </View>
  );

  if (compactLayout) {
    if (renderInline) {
      return <View style={styles.compactInlineHost}>{compactInlineEditor}</View>;
    }

    return (
      <Modal visible transparent animationType="slide" onRequestClose={props.onCancel}>
        <View style={styles.modalBackdrop}>{compactEditorCard}</View>
      </Modal>
    );
  }

  if (renderInline) {
    return <View style={styles.inlineHost}>{editorCard}</View>;
  }

  return (
    <Modal visible transparent animationType="slide" onRequestClose={props.onCancel}>
      <View style={styles.modalBackdrop}>{editorCard}</View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15, 23, 42, 0.34)",
    paddingHorizontal: 16,
    paddingVertical: 18,
    justifyContent: "center",
  },
  inlineHost: {
    flex: 1,
    minHeight: 0,
    marginTop: 10,
    marginBottom: 14,
  },
  compactInlineHost: {
    flex: 1,
    minHeight: 0,
    gap: 12,
  },
  compactInlineStack: {
    flex: 1,
    minHeight: 0,
    gap: 12,
  },
  compactInlineHeader: {
    gap: 10,
  },
  modalCard: {
    alignSelf: "center",
    width: "100%",
    maxHeight: "96%",
    borderRadius: 26,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#f7fafc",
    padding: 18,
    gap: 14,
  },
  modalCardWide: {
    maxWidth: 1480,
  },
  modalCardNarrow: {
    maxWidth: 980,
  },
  inlineCard: {
    alignSelf: "stretch",
    maxHeight: 1180,
    minHeight: 780,
  },
  inlineCardCompact: {
    alignSelf: "stretch",
    flex: 1,
    minHeight: 0,
    maxHeight: "100%",
  },
  compactInlineCard: {
    maxHeight: undefined,
  },
  compactCard: {
    alignSelf: "stretch",
    flex: 1,
    paddingHorizontal: 14,
    paddingVertical: 14,
    gap: 12,
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 16,
  },
  compactHeader: {
    gap: 10,
  },
  modalHeaderText: {
    flex: 1,
    gap: 4,
  },
  modalTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "800",
  },
  modalSubtitle: {
    color: colors.subText,
    fontSize: 13,
    lineHeight: 19,
  },
  modalHint: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 19,
  },
  modalMeta: {
    color: colors.primary,
    fontSize: 12.5,
    lineHeight: 18,
  },
  modalHeaderAside: {
    alignItems: "flex-end",
    gap: 10,
  },
  modalHeaderAsideCompact: {
    alignItems: "flex-start",
  },
  closeButton: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#cbd5e1",
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: "#ffffff",
  },
  closeButtonText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  metricRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  metricCard: {
    flex: 1,
    minWidth: 200,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 6,
  },
  metricValue: {
    color: colors.primary,
    fontSize: 22,
    fontWeight: "800",
  },
  metricLabel: {
    color: colors.subText,
    fontSize: 12.5,
    fontWeight: "700",
  },
  modeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  modeChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  modeChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  modeChipText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "700",
  },
  modeChipTextActive: {
    color: "#ffffff",
  },
  workspaceBody: {
    flex: 1,
    flexDirection: "row",
    gap: 14,
    minHeight: 0,
  },
  workspaceMain: {
    flex: 1,
    gap: 12,
    minHeight: 0,
  },
  workspaceAside: {
    width: 320,
    flexShrink: 0,
    gap: 12,
  },
  asideCard: {
    gap: 8,
  },
  asideTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  asideText: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 19,
  },
  asideMeta: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 18,
  },
  toolbarRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    alignItems: "center",
  },
  searchInput: {
    flex: 1,
    minWidth: 240,
    minHeight: 44,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  searchInputCompact: {
    minWidth: 0,
    width: "100%",
  },
  compactToolbarActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  toolbarChip: {
    borderRadius: 999,
    backgroundColor: "#eef4fb",
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  toolbarChipText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "700",
  },
  filterWrap: {
    minHeight: 38,
  },
  filterScroll: {
    gap: 8,
    paddingRight: 12,
  },
  filterChip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: "#eef3f7",
  },
  filterChipActive: {
    backgroundColor: "#d9e8ff",
  },
  filterChipText: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  filterChipTextActive: {
    color: colors.primary,
  },
  warningPanel: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#f1d7a8",
    backgroundColor: "#fff8ea",
    padding: 12,
    gap: 8,
  },
  warningTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  warningTags: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  warningTag: {
    borderRadius: 999,
    backgroundColor: "#fff1cc",
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  warningTagText: {
    color: "#8a5d00",
    fontSize: 12,
    fontWeight: "700",
  },
  editorPanel: {
    flex: 1,
    minHeight: 0,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    padding: 16,
    gap: 10,
  },
  editorPanelCompact: {
    flexGrow: 0,
    flexShrink: 0,
    minHeight: 0,
  },
  panelTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
  },
  panelHint: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  wordCanvasNative: {
    minHeight: 420,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 16,
    paddingVertical: 14,
    lineHeight: 22,
  },
  wordCanvasNativeCompact: {
    minHeight: 280,
  },
  sheetScroll: {
    flex: 1,
    minHeight: 0,
  },
  sheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 14,
    backgroundColor: "#edf3f8",
    paddingHorizontal: 8,
    paddingVertical: 10,
    gap: 8,
  },
  sheetHeaderText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "800",
  },
  sheetRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    paddingHorizontal: 8,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f7",
  },
  sheetRowAlt: {
    backgroundColor: "#fbfdff",
  },
  sheetIndexCol: {
    width: 56,
  },
  sheetSectionCol: {
    width: 120,
  },
  sheetFieldCol: {
    width: 180,
  },
  sheetValueCol: {
    flex: 1,
  },
  sheetIndexText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "800",
    paddingTop: 11,
  },
  sheetCellText: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 18,
    paddingTop: 11,
  },
  sheetInput: {
    minHeight: 44,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  sheetInputMultiline: {
    minHeight: 86,
  },
  formScroll: {
    flex: 1,
    minHeight: 0,
  },
  formScrollContent: {
    gap: 12,
    paddingBottom: 4,
  },
  sectionBlock: {
    gap: 8,
  },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  sectionTitle: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "800",
  },
  sectionMeta: {
    color: colors.subText,
    fontSize: 12.5,
  },
  fieldList: {
    gap: 10,
  },
  fieldCard: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    padding: 12,
    gap: 8,
  },
  fieldHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  fieldHeadText: {
    flex: 1,
    gap: 2,
  },
  fieldLabel: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "700",
  },
  fieldSectionLabel: {
    color: colors.subText,
    fontSize: 12,
  },
  fieldStatusRow: {
    alignItems: "flex-end",
    gap: 6,
  },
  fieldInput: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  fieldInputMultiline: {
    minHeight: 92,
  },
  emptyText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
    paddingVertical: 8,
  },
  previewScroll: {
    flex: 1,
    minHeight: 0,
  },
  previewScrollContent: {
    gap: 12,
    paddingBottom: 4,
  },
  compactScroll: {
    flex: 1,
    minHeight: 0,
  },
  compactScrollContent: {
    flexGrow: 1,
    gap: 12,
    paddingBottom: 32,
  },
  compactInlineContent: {
    gap: 12,
  },
  compactSummaryPanel: {
    gap: 8,
  },
  compactSummaryText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 20,
  },
  previewCard: {
    gap: 8,
  },
  previewTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  previewText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 21,
  },
  checklistCard: {
    gap: 6,
  },
  checklistItem: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 18,
  },
  footer: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 10,
    paddingTop: 4,
  },
  footerCompact: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    paddingTop: 4,
  },
  footerButton: {
    minWidth: 120,
  },
  footerButtonCompact: {
    minWidth: 120,
    flexGrow: 1,
  },
});
