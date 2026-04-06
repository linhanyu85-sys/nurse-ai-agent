import React, { useEffect, useMemo, useRef, useState } from "react";
import { useFocusEffect } from "@react-navigation/native";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { api, getApiErrorMessage } from "../api/endpoints";
import { subscribePatientContext } from "../api/realtime";
import { DocumentStructuredEditor } from "../components/DocumentStructuredEditor";
import { ActionButton, AnimatedBlock, CollapsibleCard, ScreenShell, StatusPill, SurfaceCard } from "../components/ui";
import type { RootStackParamList } from "../navigation/RootNavigator";
import { useAppStore } from "../store/appStore";
import { colors } from "../theme";
import type {
  DocumentDraft,
  DocumentStructuredFields,
  DocumentTemplate,
  OrderListOut,
  Patient,
  PatientContext,
  StandardFormBundle,
} from "../types";
import { buildClinicalRiskBadge } from "../utils/clinicalRisk";
import {
  buildDocumentPreviewBlocks,
  buildDocumentPreviewText,
  formatArchiveHint,
  getDraftArchiveIdentity,
  getEditableBlocks,
  getStructuredFields,
  hydrateDraftForEditing,
} from "../utils/documentDraft";
import { getDocumentTypeLabel, getSourceTypeLabel, getStatusLabel } from "../utils/displayText";
import { formatBedLabel, normalizeBedNo, normalizePersonName } from "../utils/displayValue";
import { formatAiText } from "../utils/text";

type Props = NativeStackScreenProps<RootStackParamList, "PatientDetail">;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <SurfaceCard>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </SurfaceCard>
  );
}

function ChoiceChip(props: { active: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable style={[styles.choiceChip, props.active && styles.choiceChipActive]} onPress={props.onPress}>
      <Text style={[styles.choiceChipText, props.active && styles.choiceChipTextActive]}>{props.label}</Text>
    </Pressable>
  );
}

function getDraftMergeScore(item: DocumentDraft) {
  const editableCount = getEditableBlocks(item).length;
  const sheetColumns = Array.isArray(item.structured_fields?.standard_form?.sheet_columns)
    ? item.structured_fields?.standard_form?.sheet_columns.length
    : 0;
  const updatedAt = Date.parse(String(item.updated_at || item.created_at || ""));
  const updatedWeight = Number.isFinite(updatedAt) ? Math.floor(updatedAt / 1000) : 0;
  return editableCount * 100000 + sheetColumns * 1000 + updatedWeight;
}

function mergeDraftRows(...collections: Array<DocumentDraft[] | null | undefined>) {
  const merged = new Map<string, DocumentDraft>();
  collections.forEach((collection) => {
    (collection || []).forEach((item) => {
      const current = merged.get(item.id);
      if (!current || getDraftMergeScore(item) >= getDraftMergeScore(current)) {
        merged.set(item.id, item);
      }
    });
  });
  return Array.from(merged.values()).sort((a, b) =>
    String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || ""))
  );
}

function buildPatientFallback(patientId: string, context?: PatientContext | null): Patient | null {
  if (!context?.patient_name && !context?.bed_no) {
    return null;
  }
  return {
    id: patientId,
    mrn: "",
    inpatient_no: "",
    full_name: context.patient_name || "",
    current_status: "active",
  };
}

function isDraftRelatedToScope(
  item: DocumentDraft,
  scope: {
    patientIds: Set<string>;
    bedNo?: string;
    patientName?: string;
  }
) {
  const normalizedBedNo = String(scope.bedNo || "").trim();
  const normalizedPatientName = normalizePersonName(scope.patientName, "");
  const identity = getDraftArchiveIdentity(item, {
    bedNo: normalizedBedNo,
    patientName: normalizedPatientName,
  });
  const itemPatientIds = [String(item.patient_id || "").trim(), String(identity.patientId || "").trim()].filter(Boolean);
  if (itemPatientIds.some((patientId) => scope.patientIds.has(patientId))) {
    return true;
  }
  if (scope.patientIds.size > 0 && itemPatientIds.length > 0) {
    return false;
  }
  if (normalizedBedNo && String(identity.bedNo || "").trim() === normalizedBedNo) {
    return true;
  }
  if (normalizedPatientName && normalizePersonName(identity.patientName, "") === normalizedPatientName) {
    return true;
  }
  return false;
}

export function PatientDetailScreen({ navigation, route }: Props) {
  const { patientId: routePatientId, bedNo: routeBedNo } = route.params;
  const normalizedRouteBedNo = normalizeBedNo(routeBedNo);
  const user = useAppStore((state) => state.user);
  const departmentId = useAppStore((state) => state.selectedDepartmentId);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [context, setContext] = useState<PatientContext | null>(null);
  const [documents, setDocuments] = useState<DocumentDraft[]>([]);
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [standardForms, setStandardForms] = useState<StandardFormBundle[]>([]);
  const [orderList, setOrderList] = useState<OrderListOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [docBusy, setDocBusy] = useState(false);
  const [error, setError] = useState("");
  const [streamStatus, setStreamStatus] = useState("未连接");
  const [lastPushAt, setLastPushAt] = useState<string>("-");
  const [docSearch, setDocSearch] = useState("");
  const [composerText, setComposerText] = useState("");
  const [selectedDocumentType, setSelectedDocumentType] = useState("nursing_note");
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [editingDraft, setEditingDraft] = useState<DocumentDraft | null>(null);
  const [draftText, setDraftText] = useState("");
  const [editingStructuredFields, setEditingStructuredFields] = useState<DocumentStructuredFields>({});
  const [contextIssue, setContextIssue] = useState("");
  const [orderIssue, setOrderIssue] = useState("");
  const [documentIssue, setDocumentIssue] = useState("");
  const [templateIssue, setTemplateIssue] = useState("");
  const [resolvedPatientId, setResolvedPatientId] = useState(routePatientId);
  const hasLoadedOnceRef = useRef(false);
  const lastRefreshRef = useRef(0);
  const loadRequestRef = useRef(0);

  const patientId = resolvedPatientId || routePatientId;

  useEffect(() => {
    setResolvedPatientId(routePatientId);
  }, [routePatientId, routeBedNo]);

  const loadDetail = async (options?: { silent?: boolean }) => {
    const requestId = ++loadRequestRef.current;
    const silent = Boolean(options?.silent && hasLoadedOnceRef.current);
    const finishPrimaryLoad = () => {
      if (requestId !== loadRequestRef.current) {
        return;
      }
      hasLoadedOnceRef.current = true;
      lastRefreshRef.current = Date.now();
      if (!silent) {
        setLoading(false);
      }
    };

    if (!silent) {
      setLoading(true);
    }
    setError("");
    setContextIssue("");
    setOrderIssue("");
    setDocumentIssue("");
    setTemplateIssue("");

    const templateLoadPromise = Promise.allSettled([api.listDocumentTemplates(), api.listStandardForms()]);

    try {
      let targetPatientId = routePatientId;
      let seededContext: PatientContext | null = null;
      if (normalizedRouteBedNo) {
        try {
          const bedContext = await api.getBedContext(normalizedRouteBedNo, {
            departmentId,
            requestedBy: user?.id,
          });
          seededContext = bedContext;
          if (bedContext.patient_id) {
            targetPatientId = bedContext.patient_id;
          }
        } catch {
          seededContext = null;
        }
      }
      if (requestId !== loadRequestRef.current) {
        return;
      }

      setResolvedPatientId(targetPatientId);
      const [patientResult, contextResult, orderResult, documentResult, inboxResult] = await Promise.allSettled([
        api.getPatient(targetPatientId),
        seededContext ? Promise.resolve(seededContext) : api.getPatientContext(targetPatientId, user?.id),
        api.getPatientOrders(targetPatientId),
        api.listDocumentHistory(targetPatientId, 24, user?.id),
        user?.id ? api.getDocumentInbox(user.id, { patientId: targetPatientId, limit: 24 }) : Promise.resolve([]),
      ]);
      if (requestId !== loadRequestRef.current) {
        return;
      }

      if (patientResult.status === "fulfilled") {
        setPatient(patientResult.value);
      } else {
        const fallbackPatient =
          contextResult.status === "fulfilled" ? buildPatientFallback(targetPatientId, contextResult.value) : buildPatientFallback(targetPatientId, seededContext);
        setPatient(fallbackPatient);
        if (!fallbackPatient) {
          setError(getApiErrorMessage(patientResult.reason, "患者基础档案未返回，页面继续展示当前文书与上下文。"));
        }
      }

      if (contextResult.status === "fulfilled") {
        setContext(contextResult.value);
      } else {
        setContext(null);
        setContextIssue(getApiErrorMessage(contextResult.reason, "患者上下文未返回，当前不展示风险分层与最新观察。"));
      }

      if (orderResult.status === "fulfilled") {
        setOrderList(orderResult.value);
      } else {
        setOrderList(null);
        setOrderIssue(getApiErrorMessage(orderResult.reason, "医嘱执行接口未返回，当前暂停展示医嘱概览。"));
      }

      const historyRows = documentResult.status === "fulfilled" ? documentResult.value : [];
      const inboxRows = inboxResult.status === "fulfilled" ? inboxResult.value : [];
      setDocuments(mergeDraftRows(historyRows, inboxRows));

      if (documentResult.status === "rejected" || inboxResult.status === "rejected") {
        const issues = [
          documentResult.status === "rejected"
            ? getApiErrorMessage(documentResult.reason, "文书历史接口未返回，页面暂不把空白当成“无文书”。")
            : "",
          inboxResult.status === "rejected"
            ? getApiErrorMessage(inboxResult.reason, "草稿箱接口未返回，页面暂不把空白当成“无草稿”。")
            : "",
        ].filter(Boolean);
        setDocumentIssue(issues.join("；"));
      } else {
        setDocumentIssue("");
      }

      const resolvedContext = contextResult.status === "fulfilled" ? contextResult.value : seededContext;
      const resolvedPatient = patientResult.status === "fulfilled" ? patientResult.value : null;
      const relatedPatientIds = new Set(
        [routePatientId, targetPatientId, seededContext?.patient_id, resolvedContext?.patient_id]
          .map((item) => String(item || "").trim())
          .filter(Boolean)
      );
      const relatedBedNo = normalizeBedNo(resolvedContext?.bed_no || normalizedRouteBedNo || "");
      const relatedPatientName = normalizePersonName(resolvedPatient?.full_name || resolvedContext?.patient_name, "");

      if (relatedPatientIds.size > 0 || relatedBedNo || relatedPatientName) {
        void (async () => {
          try {
            const [nextHistoryRows, nextInboxRows] = await Promise.all([
              api.listDocumentHistory("", 120, user?.id),
              user?.id ? api.getDocumentInbox(user.id, { limit: 120 }) : Promise.resolve([]),
            ]);
            const relatedRows = [...nextHistoryRows, ...nextInboxRows].filter((item) =>
              isDraftRelatedToScope(item, {
                patientIds: relatedPatientIds,
                bedNo: relatedBedNo,
                patientName: relatedPatientName,
              })
            );
            if (requestId !== loadRequestRef.current || !relatedRows.length) {
              return;
            }
            setDocuments((current) => mergeDraftRows(current, relatedRows));
          } catch {
            // Best-effort enrichment only; do not block first paint.
          }
        })();
      }

      if (patientResult.status === "fulfilled" || contextResult.status === "fulfilled" || historyRows.length || inboxRows.length) {
        setStreamStatus((current) => (current === "已连接" || current === "同步正常" ? current : "静态查看"));
      }

      finishPrimaryLoad();

      void templateLoadPromise.then(([templateResult, formResult]) => {
        if (requestId !== loadRequestRef.current) {
          return;
        }
        const availableForms = formResult.status === "fulfilled" ? formResult.value : [];
        setTemplates(templateResult.status === "fulfilled" ? templateResult.value : []);
        setStandardForms(formResult.status === "fulfilled" ? formResult.value : []);
        if (formResult.status === "rejected") {
          setTemplateIssue(getApiErrorMessage(formResult.reason, "标准表单定义暂未返回，当前无法完整展示模板字段结构。"));
        } else if (templateResult.status === "rejected" && !availableForms.length) {
          setTemplateIssue(getApiErrorMessage(templateResult.reason, "文书模板库暂未返回，当前仅保留已生成草稿与已归档文书。"));
        } else {
          setTemplateIssue("");
        }
      });
    } catch (err) {
      if (requestId !== loadRequestRef.current) {
        return;
      }
      setError(getApiErrorMessage(err, "患者档案加载失败，请稍后重试。"));
    } finally {
      finishPrimaryLoad();
    }
  };

  useEffect(() => {
    void loadDetail();
  }, [routePatientId, routeBedNo, departmentId, user?.id]);

  useFocusEffect(
    React.useCallback(() => {
      if (!hasLoadedOnceRef.current) {
        return undefined;
      }
      const now = Date.now();
      if (now - lastRefreshRef.current < 1200) {
        return undefined;
      }
      void loadDetail({ silent: true });
      return undefined;
    }, [departmentId, routeBedNo, routePatientId, user?.id])
  );

  useEffect(() => {
    const unsubscribe = subscribePatientContext(
      patientId,
      (payload) => {
        if (payload?.type === "patient_context_update" && payload?.data) {
          setContext(payload.data);
          setLastPushAt(new Date().toLocaleTimeString());
          setStreamStatus("已连接");
        } else if (payload?.type === "heartbeat") {
          setStreamStatus("同步正常");
          setLastPushAt(new Date().toLocaleTimeString());
        }
      },
      () => setStreamStatus("静态查看")
    );
    return unsubscribe;
  }, [patientId]);

  useEffect(() => {
    if (!patient && !context && !documents.length) {
      return;
    }
    setStreamStatus((current) => {
      if (current === "已连接" || current === "同步正常") {
        return current;
      }
      return "静态查看";
    });
  }, [context, documents.length, patient]);

  useEffect(() => {
    const available = standardForms.map((item) => item.document_type);
    if (available.length && !available.includes(selectedDocumentType)) {
      setSelectedDocumentType(available[0]);
    }
  }, [selectedDocumentType, standardForms]);

  const filteredDocuments = useMemo(() => {
    const keyword = docSearch.trim().toLowerCase();
    const rows = [...documents].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    if (!keyword) {
      return rows;
    }
    return rows.filter((item) => {
      const blocks = getEditableBlocks(item)
        .map((block) => `${block.label} ${block.value}`)
        .join(" ");
      const haystack = `${getDocumentTypeLabel(item.document_type)} ${item.draft_text} ${getStatusLabel(item.status)} ${getSourceTypeLabel(
        item.source_type || ""
      )} ${blocks}`.toLowerCase();
      return haystack.includes(keyword);
    });
  }, [documents, docSearch]);

  const draftDocuments = useMemo(() => filteredDocuments.filter((item) => item.status !== "submitted"), [filteredDocuments]);
  const archivedDocuments = useMemo(() => filteredDocuments.filter((item) => item.status === "submitted"), [filteredDocuments]);

  const documentStats = useMemo(() => {
    const missingFields = draftDocuments.reduce((sum, item) => sum + Number(item.structured_fields?.field_summary?.missing || 0), 0);
    const reviewedCount = draftDocuments.filter((item) => item.status === "reviewed").length;
    return {
      draftCount: draftDocuments.length,
      missingFields,
      reviewedCount,
      archivedCount: archivedDocuments.length,
    };
  }, [archivedDocuments.length, draftDocuments]);

  const availableDocumentTypes = useMemo(() => {
    if (standardForms.length) {
      return standardForms.map((item) => item.document_type);
    }
    return Array.from(new Set(templates.map((item) => item.document_type).filter(Boolean))) as string[];
  }, [standardForms, templates]);

  const selectedForm = useMemo(
    () => standardForms.find((item) => item.document_type === selectedDocumentType) || null,
    [selectedDocumentType, standardForms]
  );

  const lockedTemplate = useMemo(
    () =>
      templates.find(
        (item) => item.source_type === "system" && (item.document_type || "nursing_note") === selectedDocumentType
      ) || null,
    [selectedDocumentType, templates]
  );

  const availableTemplatesForType = useMemo(() => {
    const exact = templates.filter((item) => (item.document_type || "nursing_note") === selectedDocumentType);
    if (exact.length) {
      return exact;
    }
    return lockedTemplate ? [lockedTemplate] : [];
  }, [lockedTemplate, selectedDocumentType, templates]);

  const selectedTemplate = useMemo(() => {
    if (selectedTemplateId) {
      const matched = availableTemplatesForType.find((item) => item.id === selectedTemplateId);
      if (matched) {
        return matched;
      }
    }
    return availableTemplatesForType[0] || lockedTemplate || null;
  }, [availableTemplatesForType, lockedTemplate, selectedTemplateId]);

  useEffect(() => {
    if (!availableTemplatesForType.length) {
      if (selectedTemplateId) {
        setSelectedTemplateId("");
      }
      return;
    }
    if (!selectedTemplateId || !availableTemplatesForType.some((item) => item.id === selectedTemplateId)) {
      setSelectedTemplateId(availableTemplatesForType[0].id);
    }
  }, [availableTemplatesForType, selectedTemplateId]);

  const openEditor = (draft: DocumentDraft) => {
    const identity = getDraftArchiveIdentity(draft, {
      bedNo: normalizeBedNo(context?.bed_no || routeBedNo),
      patientName: normalizePersonName(patient?.full_name || context?.patient_name, ""),
      patientId,
    });
    navigation.push("DocumentEditor", {
      patientId: identity.patientId || draft.patient_id || patientId,
      bedNo: identity.bedNo || normalizeBedNo(context?.bed_no || routeBedNo),
      draftId: draft.id,
      initialDraft: draft,
    });
  };

  const cancelEditor = () => {
    setEditingDraft(null);
    setDraftText("");
    setEditingStructuredFields({});
  };

  const saveDraftEdit = async () => {
    if (!editingDraft) {
      return;
    }
    try {
      setDocBusy(true);
      await api.editDraft(editingDraft.id, {
        draftText,
        editedBy: user?.id,
        structuredFields: editingStructuredFields,
      });
      cancelEditor();
      await loadDetail({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "文书保存失败，请稍后重试。"));
    } finally {
      setDocBusy(false);
    }
  };

  const reviewDraft = async (draftId: string) => {
    try {
      setDocBusy(true);
      await api.reviewDraft(draftId, user?.id || "u_nurse_01");
      await loadDetail({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "文书审核失败，请稍后重试。"));
    } finally {
      setDocBusy(false);
    }
  };

  const submitDraft = async (draftId: string) => {
    try {
      setDocBusy(true);
      await api.submitDraft(draftId, user?.id || "u_nurse_01");
      if (editingDraft?.id === draftId) {
        cancelEditor();
      }
      await loadDetail({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "文书归档失败，请稍后重试。"));
    } finally {
      setDocBusy(false);
    }
  };

  const createDraft = async () => {
    try {
      setDocBusy(true);
      setError("");
      const draft = await api.createDocumentDraft(patientId, composerText.trim(), {
        documentType: selectedDocumentType,
        templateId: selectedTemplate?.id,
        templateText: selectedTemplate?.template_text,
        templateName: selectedTemplate?.name || selectedForm?.name,
        requestedBy: user?.id,
        bedNo: context?.bed_no,
        patientName: patient?.full_name || context?.patient_name,
      });
      const seededDraft = selectedForm
        ? hydrateDraftForEditing(draft, {
            standardForm: selectedForm,
            patient,
            context,
          })
        : draft;
      setDocuments((current) => [seededDraft, ...current.filter((item) => item.id !== seededDraft.id)]);
      setComposerText("");
      openEditor(seededDraft);
      void loadDetail({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "文书草稿生成失败，请检查模板服务与模型连接。"));
    } finally {
      setDocBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  const risk = buildClinicalRiskBadge(context || {});
  const title = normalizePersonName(patient?.full_name || context?.patient_name, "病例详情");
  const subtitleParts = [
    context?.bed_no ? formatBedLabel(context.bed_no) : "",
    patient?.gender || "",
    patient?.age ? `${patient.age}岁` : "",
    patient?.blood_type ? `血型 ${patient.blood_type}` : "",
  ].filter(Boolean);

  const hasRenderableData = Boolean(patient || context || documents.length);
  const displayStreamStatus =
    hasRenderableData && streamStatus !== "已连接" && streamStatus !== "同步正常" ? "静态查看" : streamStatus;
  const displayStreamTone = displayStreamStatus === "连接异常" && !hasRenderableData ? "danger" : "info";
  const showStreamStatus = displayStreamStatus === "已连接" || displayStreamStatus === "同步正常";
  const resolvedFriendlyError =
    error.includes("patient_not_found") && hasRenderableData
      ? ""
      : error.includes("patient_not_found")
      ? "未找到当前床位对应的患者档案，页面先保留草稿与上下文视图，请返回草稿列表重新进入。"
      : error;

  return (
    <ScreenShell
      title={title}
      subtitle={subtitleParts.join(" · ")}
      rightNode={showStreamStatus ? <StatusPill text={displayStreamStatus} tone={displayStreamTone} /> : null}
    >
      {resolvedFriendlyError ? <Text style={styles.errorText}>{resolvedFriendlyError}</Text> : null}

      <AnimatedBlock delay={0}>
        <SurfaceCard>
          <Text style={styles.info}>病案号：{patient?.mrn || "-"}</Text>
          <Text style={styles.info}>住院号：{patient?.inpatient_no || "-"}</Text>
          <Text style={styles.info}>过敏史：{patient?.allergy_info || "无"}</Text>
          {showStreamStatus && lastPushAt !== "-" ? <Text style={styles.info}>最近同步：{lastPushAt}</Text> : null}
          {context?.latest_document_sync ? <Text style={styles.highlightText}>{context.latest_document_sync}</Text> : null}
        </SurfaceCard>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <SurfaceCard>
          <View style={styles.riskHead}>
            <Text style={styles.sectionTitle}>临床风险分层</Text>
            <View style={styles.riskPills}>
              <StatusPill text={risk.source === "structured" ? "已核实" : "待核对"} tone={risk.source === "structured" ? "success" : "warning"} />
              <StatusPill text={risk.label} tone={risk.tone} />
            </View>
          </View>
          <Text style={styles.item}>风险依据：{risk.reason}</Text>
          {risk.warning ? <Text style={styles.warningText}>{risk.warning}</Text> : null}
          {risk.score !== null ? <Text style={styles.info}>风险分值：{String(risk.score)}</Text> : null}
        </SurfaceCard>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <Section title="诊断">
          {(context?.diagnoses || []).length ? (
            (context?.diagnoses || []).map((item) => (
              <Text key={item} style={styles.item}>
                - {item}
              </Text>
            ))
          ) : (
            <Text style={styles.info}>暂无诊断信息</Text>
          )}
        </Section>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <Section title="风险标签">
          {(context?.risk_tags || []).length ? (
            (context?.risk_tags || []).map((item) => (
              <Text key={item} style={styles.item}>
                - {item}
              </Text>
            ))
          ) : (
            <Text style={styles.info}>暂无重点风险标签</Text>
          )}
        </Section>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <Section title="待处理任务">
          {(context?.pending_tasks || []).length ? (
            (context?.pending_tasks || []).map((item) => (
              <Text key={item} style={styles.item}>
                - {item}
              </Text>
            ))
          ) : (
            <Text style={styles.info}>当前没有待处理任务</Text>
          )}
        </Section>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <Section title="最新观察">
          {(context?.latest_observations || []).length ? (
            (context?.latest_observations || []).map((item, idx) => (
              <Text key={`${item.name}-${idx}`} style={styles.item}>
                - {item.name}：{item.value}
                {item.abnormal_flag ? `（${item.abnormal_flag}）` : ""}
              </Text>
            ))
          ) : (
            <Text style={styles.info}>暂无最新观察数据</Text>
          )}
        </Section>
      </AnimatedBlock>

      {orderList ? (
        <AnimatedBlock delay={0}>
          <Section title="医嘱执行概览">
            <Text style={styles.item}>
              待执行：{orderList.stats.pending} · 30 分钟到时：{orderList.stats.due_30m} · 超时：{orderList.stats.overdue}
            </Text>
            <Text style={styles.item}>高警示医嘱：{orderList.stats.high_alert}</Text>
            {(orderList.orders || []).slice(0, 3).map((order) => (
              <Text key={order.id} style={styles.item}>
                - {order.priority} · {order.title}（{order.status}）
              </Text>
            ))}
          </Section>
        </AnimatedBlock>
      ) : null}

      <AnimatedBlock delay={0}>
        <Section title="新建护理文书">
          <Text style={styles.subSectionTitle}>文书类型</Text>
          <View style={styles.choiceWrap}>
            {availableDocumentTypes.map((item) => (
              <ChoiceChip key={item} active={item === selectedDocumentType} label={getDocumentTypeLabel(item)} onPress={() => setSelectedDocumentType(item)} />
            ))}
          </View>

          {selectedForm ? (
            <View style={styles.templateMetaCard}>
              <Text style={styles.templateMetaTitle}>{selectedForm.name}</Text>
              {selectedForm.source_refs?.length ? <Text style={styles.templateMetaText}>来源：{selectedForm.source_refs.join(" / ")}</Text> : null}
              <Text style={styles.templateMetaText}>字段数：{selectedForm.field_count}</Text>
            </View>
          ) : null}

          <Text style={styles.subSectionTitle}>模板选择</Text>
          {availableTemplatesForType.length ? (
            <View style={styles.choiceWrap}>
              {availableTemplatesForType.map((item) => (
                <ChoiceChip
                  key={item.id}
                  active={item.id === selectedTemplate?.id}
                  label={`${item.name}${item.source_type === "import" ? " · 导入" : " · 内置"}`}
                  onPress={() => setSelectedTemplateId(item.id)}
                />
              ))}
            </View>
          ) : (
            <Text style={styles.metaText}>当前暂无模板，将按标准表单创建草稿。</Text>
          )}

          <View style={styles.templatePreviewCard}>
            <Text style={styles.templatePreviewTitle}>{selectedTemplate?.name || selectedForm?.name || "标准护理文书模板"}</Text>
            {(selectedTemplate?.source_refs?.length || selectedForm?.source_refs?.length) ? (
              <Text style={styles.templateMetaText}>
                来源：{(selectedTemplate?.source_refs?.length ? selectedTemplate.source_refs : selectedForm?.source_refs || []).join(" / ")}
              </Text>
            ) : null}
            {selectedTemplate ? <Text style={styles.templateMetaText}>当前模板：{getSourceTypeLabel(selectedTemplate.source_type)}</Text> : null}
            {selectedTemplate ? (
              <Text style={styles.templatePreviewText} numberOfLines={10}>
                {formatAiText(selectedTemplate.template_text)}
              </Text>
            ) : null}
          </View>

          <Text style={styles.subSectionTitle}>护理要点 / 交班摘要</Text>
          <TextInput
            value={composerText}
            onChangeText={setComposerText}
            placeholder="例如：患者 12 床，今日输血后无寒战发热，需继续观察生命体征并记录输血结束情况。"
            placeholderTextColor={colors.subText}
            multiline
            style={styles.composerInput}
            textAlignVertical="top"
          />

          <View style={styles.actionRow}>
            <ActionButton label={composerText.trim() ? "生成文书草稿" : "按模板新建并编辑"} onPress={createDraft} disabled={docBusy} />
            <ActionButton label="清空" onPress={() => setComposerText("")} variant="secondary" disabled={docBusy} />
          </View>
        </Section>
      </AnimatedBlock>

      <AnimatedBlock delay={0}>
        <Section title={`文书档案${context?.bed_no ? ` · ${formatBedLabel(context.bed_no)}` : ""}`}>
          <View style={styles.docMetricRow}>
            <View style={styles.docMetricCard}>
              <Text style={styles.docMetricValue}>{documentStats.draftCount}</Text>
              <Text style={styles.docMetricLabel}>草稿</Text>
            </View>
            <View style={styles.docMetricCard}>
              <Text style={styles.docMetricValue}>{documentStats.missingFields}</Text>
              <Text style={styles.docMetricLabel}>待补字段</Text>
            </View>
            <View style={styles.docMetricCard}>
              <Text style={styles.docMetricValue}>{documentStats.reviewedCount}</Text>
              <Text style={styles.docMetricLabel}>待归档</Text>
            </View>
            <View style={styles.docMetricCard}>
              <Text style={styles.docMetricValue}>{documentStats.archivedCount}</Text>
              <Text style={styles.docMetricLabel}>已归档</Text>
            </View>
          </View>

          <TextInput
            value={docSearch}
            onChangeText={setDocSearch}
            placeholder="搜索文书内容、类型或状态"
            placeholderTextColor={colors.subText}
            style={styles.searchInput}
          />

          {Platform.OS !== "web" && editingDraft ? (
            <DocumentStructuredEditor
              key={editingDraft.id}
              draft={editingDraft}
              draftText={draftText}
              structuredFields={editingStructuredFields}
              busy={docBusy}
              onDraftTextChange={setDraftText}
              onStructuredFieldsChange={setEditingStructuredFields}
              onCancel={cancelEditor}
              onSave={saveDraftEdit}
            />
          ) : null}

          <CollapsibleCard
            title={`草稿区 (${draftDocuments.length})`}
            subtitle="可继续编辑、审核和归档"
            defaultExpanded
            badge={<StatusPill text="待处理" tone={draftDocuments.length ? "warning" : "info"} />}
            style={styles.docSection}
          >
            {!draftDocuments.length ? (
              <Text style={styles.info}>当前没有命中的草稿文书。</Text>
            ) : (
              draftDocuments.map((item) => {
                const fieldSummary = item.structured_fields?.field_summary;
                const standardName = item.structured_fields?.standard_form?.name;
                const previewBlocks = buildDocumentPreviewBlocks(item);
                const previewText = previewBlocks.length ? "" : buildDocumentPreviewText(item);
                const isEditing = editingDraft?.id === item.id;
                return (
                  <View key={item.id} style={styles.documentCardStack}>
                    <View style={[styles.documentRow, isEditing && styles.documentRowActive]}>
                    <View style={styles.documentHead}>
                      <Text style={styles.documentTitle}>{getDocumentTypeLabel(item.document_type)}</Text>
                      <StatusPill text={getStatusLabel(item.status)} tone={item.status === "reviewed" ? "info" : "warning"} />
                    </View>
                    <Text style={styles.documentMeta}>
                      {getSourceTypeLabel(item.source_type || "ai")} · {item.updated_at || item.created_at || "-"}
                    </Text>
                    <Text style={styles.documentMeta}>{formatArchiveHint(item)}</Text>
                    {standardName ? <Text style={styles.documentMeta}>标准模板：{standardName}</Text> : null}
                    {item.structured_fields?.template_locked ? <Text style={styles.documentMeta}>格式策略：已锁定标准模板填写</Text> : null}
                    {fieldSummary ? (
                      <Text style={styles.documentMeta}>
                        可编辑字段 {fieldSummary.total || 0} 项 · 已填 {fieldSummary.filled || 0} 项 · 待补 {fieldSummary.missing || 0} 项
                      </Text>
                    ) : null}
                    {previewBlocks.length ? (
                      <View style={styles.documentFieldGrid}>
                        {previewBlocks.map((block) => (
                          <View key={`${item.id}-${block.key}`} style={styles.documentFieldCard}>
                            <Text style={styles.documentFieldLabel}>{block.label}</Text>
                            <Text style={styles.documentFieldValue}>{formatAiText(block.value)}</Text>
                          </View>
                        ))}
                      </View>
                    ) : null}
                    {previewText ? <Text style={styles.documentText}>{formatAiText(previewText)}</Text> : null}
                    <View style={styles.actionRow}>
                      <ActionButton
                        label="打开专业编辑页"
                        onPress={() => openEditor(item)}
                        variant="secondary"
                        disabled={docBusy}
                      />
                      {item.status === "draft" ? <ActionButton label="审核" onPress={() => reviewDraft(item.id)} disabled={docBusy} /> : null}
                      <ActionButton label="归档" onPress={() => submitDraft(item.id)} disabled={docBusy} />
                    </View>
                    </View>
                    {Platform.OS === "web" && isEditing ? (
                      <View style={styles.inlineEditorDock}>
                        <View style={styles.inlineEditorBanner}>
                          <Text style={styles.inlineEditorTitle}>编辑工作台已展开</Text>
                          <Text style={styles.inlineEditorText}>
                            当前文书已经切到标准模板工作台，可直接在 Word 正文、Excel 表格和结构化字段之间切换编辑。
                          </Text>
                        </View>
                        <DocumentStructuredEditor
                          key={editingDraft.id}
                          draft={editingDraft}
                          draftText={draftText}
                          structuredFields={editingStructuredFields}
                          busy={docBusy}
                          onDraftTextChange={setDraftText}
                          onStructuredFieldsChange={setEditingStructuredFields}
                          onCancel={cancelEditor}
                          onSave={saveDraftEdit}
                        />
                      </View>
                    ) : null}
                  </View>
                );
              })
            )}
          </CollapsibleCard>

          <CollapsibleCard
            title={`已归档 (${archivedDocuments.length})`}
            subtitle="已提交到当前患者病例下"
            badge={<StatusPill text="病例归档" tone="success" />}
            style={styles.docSection}
          >
            {!archivedDocuments.length ? (
              <Text style={styles.info}>当前还没有已归档文书。</Text>
            ) : (
              archivedDocuments.map((item) => {
                const previewBlocks = buildDocumentPreviewBlocks(item, 4);
                const previewText = previewBlocks.length ? "" : buildDocumentPreviewText(item, 4);
                return (
                  <View key={item.id} style={styles.documentRow}>
                    <View style={styles.documentHead}>
                      <Text style={styles.documentTitle}>{getDocumentTypeLabel(item.document_type)}</Text>
                      <StatusPill text={getStatusLabel(item.status)} tone="success" />
                    </View>
                    <Text style={styles.documentMeta}>
                      {getSourceTypeLabel(item.source_type || "ai")} · {item.updated_at || item.created_at || "-"}
                    </Text>
                    <Text style={styles.documentMeta}>{formatArchiveHint(item)}</Text>
                    {previewBlocks.length ? (
                      <View style={styles.documentFieldGrid}>
                        {previewBlocks.map((block) => (
                          <View key={`${item.id}-${block.key}`} style={styles.documentFieldCard}>
                            <Text style={styles.documentFieldLabel}>{block.label}</Text>
                            <Text style={styles.documentFieldValue}>{formatAiText(block.value)}</Text>
                          </View>
                        ))}
                      </View>
                    ) : null}
                    {previewText ? <Text style={styles.documentText}>{formatAiText(previewText)}</Text> : null}
                  </View>
                );
              })
            )}
          </CollapsibleCard>
        </Section>
      </AnimatedBlock>
    </ScreenShell>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: colors.bg,
  },
  errorText: {
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
  riskHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    flexWrap: "wrap",
    gap: 12,
  },
  riskPills: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-start",
    gap: 6,
    maxWidth: "100%",
  },
  sectionTitle: {
    color: colors.primary,
    fontWeight: "700",
    marginBottom: 8,
  },
  subSectionTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 8,
  },
  info: {
    color: colors.subText,
    fontSize: 14,
    lineHeight: 21,
  },
  metaText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
    marginBottom: 10,
  },
  item: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
    marginBottom: 4,
  },
  warningText: {
    color: "#a66300",
    fontSize: 12.5,
    lineHeight: 18,
    fontWeight: "700",
  },
  highlightText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "700",
    marginTop: 8,
  },
  searchInput: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 12,
  },
  choiceWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 12,
  },
  choiceChip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: "#eef3f7",
    borderWidth: 1,
    borderColor: "#d7e0e2",
  },
  choiceChipActive: {
    backgroundColor: "#d9e8ff",
    borderColor: "#a9c9ff",
  },
  choiceChipText: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  choiceChipTextActive: {
    color: colors.primary,
  },
  templateMetaCard: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#f8fbff",
    padding: 12,
    marginBottom: 12,
  },
  templateMetaTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
    marginBottom: 6,
  },
  templateMetaText: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 18,
  },
  templatePreviewCard: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#e3e8ee",
    backgroundColor: "#ffffff",
    padding: 12,
    marginBottom: 12,
  },
  templatePreviewTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 6,
  },
  templatePreviewText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  composerInput: {
    minHeight: 120,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 12,
    marginBottom: 12,
  },
  docSection: {
    marginTop: 12,
  },
  documentCardStack: {
    marginTop: 10,
    gap: 10,
  },
  docMetricRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 12,
  },
  docMetricCard: {
    flex: 1,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#f8fbff",
    paddingVertical: 10,
    paddingHorizontal: 8,
    alignItems: "center",
    gap: 3,
  },
  docMetricValue: {
    color: colors.primary,
    fontSize: 18,
    fontWeight: "800",
  },
  docMetricLabel: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  documentRow: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    padding: 12,
    gap: 6,
  },
  documentRowActive: {
    borderColor: "#8fb6ff",
    backgroundColor: "#f8fbff",
  },
  documentHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  documentTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
    flex: 1,
  },
  documentMeta: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 18,
  },
  documentText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 20,
  },
  inlineEditorDock: {
    gap: 10,
  },
  inlineEditorBanner: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e6ff",
    backgroundColor: "#eef5ff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 4,
  },
  inlineEditorTitle: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "800",
  },
  inlineEditorText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  documentFieldGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  documentFieldCard: {
    minWidth: 180,
    flexGrow: 1,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#e3e8ee",
    backgroundColor: "#f8fbff",
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 4,
  },
  documentFieldLabel: {
    color: colors.subText,
    fontSize: 11.5,
    fontWeight: "700",
  },
  documentFieldValue: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 18,
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
});
