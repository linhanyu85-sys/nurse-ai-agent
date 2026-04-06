import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { api, getApiErrorMessage } from "../api/endpoints";
import { DocumentStructuredEditor } from "../components/DocumentStructuredEditor";
import { ActionButton, InfoBanner, ScreenShell, StatusPill, SurfaceCard } from "../components/ui";
import type { RootStackParamList } from "../navigation/RootNavigator";
import { useAppStore } from "../store/appStore";
import { colors } from "../theme";
import type { DocumentDraft, DocumentStructuredFields, Patient, PatientContext } from "../types";
import { buildClinicalRiskBadge } from "../utils/clinicalRisk";
import { formatArchiveHint, getEditableBlocks, getStructuredFields, hydrateDraftForEditing } from "../utils/documentDraft";
import { getDocumentTypeLabel, getStatusLabel } from "../utils/displayText";
import { formatBedLabel, normalizeBedNo, normalizePersonName } from "../utils/displayValue";

type Props = NativeStackScreenProps<RootStackParamList, "DocumentEditor">;

function SummaryItem(props: { label: string; value: string }) {
  return (
    <View style={styles.summaryItem}>
      <Text style={styles.summaryLabel}>{props.label}</Text>
      <Text style={styles.summaryValue}>{props.value}</Text>
    </View>
  );
}

function WorkbenchCard(props: { title: string; description: string }) {
  return (
    <SurfaceCard style={styles.workbenchCard}>
      <Text style={styles.workbenchTitle}>{props.title}</Text>
      <Text style={styles.workbenchText}>{props.description}</Text>
    </SurfaceCard>
  );
}

function applyDraftState(
  nextDraft: DocumentDraft,
  setDraft: React.Dispatch<React.SetStateAction<DocumentDraft | null>>,
  setDraftText: React.Dispatch<React.SetStateAction<string>>,
  setStructuredFields: React.Dispatch<React.SetStateAction<DocumentStructuredFields>>
) {
  setDraft(nextDraft);
  setDraftText(nextDraft.draft_text);
  setStructuredFields(getStructuredFields(nextDraft));
}

function getDraftQualityScore(item?: DocumentDraft | null) {
  if (!item) {
    return -1;
  }
  const structured = getStructuredFields(item);
  const editableCount = getEditableBlocks(item).length;
  const sheetColumns = Array.isArray(structured.standard_form?.sheet_columns) ? structured.standard_form.sheet_columns.length : 0;
  const summaryTotal = Number(structured.field_summary?.total || 0);
  const templateSnapshot = String(structured.template_snapshot || "").trim() ? 1 : 0;
  const updatedAt = Date.parse(String(item.updated_at || ""));
  const updatedWeight = Number.isFinite(updatedAt) ? Math.floor(updatedAt / 1000) : 0;
  return editableCount * 100000 + sheetColumns * 1000 + summaryTotal * 10 + templateSnapshot + updatedWeight;
}

function pickPreferredDraft(candidates: Array<DocumentDraft | null | undefined>) {
  return candidates.reduce<DocumentDraft | null>((best, current) => {
    if (!current) {
      return best;
    }
    if (!best) {
      return current;
    }
    return getDraftQualityScore(current) >= getDraftQualityScore(best) ? current : best;
  }, null);
}

function resolveStandardFormLookupKey(item: DocumentDraft) {
  const structured = getStructuredFields(item);
  const candidates = [
    structured.document_type,
    structured.template_name,
    structured.standard_form?.name,
    item.document_type,
  ];
  for (const candidate of candidates) {
    const value = String(candidate || "").trim();
    if (value) {
      return value;
    }
  }
  return item.document_type;
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

function getDraftFieldText(item: DocumentDraft | null | undefined, keys: string[]) {
  if (!item) {
    return "";
  }
  const structured = getStructuredFields(item);
  for (const key of keys) {
    const direct = structured?.[key as keyof DocumentStructuredFields];
    if (typeof direct === "string" && direct.trim()) {
      return direct.trim();
    }
    const blockValue = structured?.editable_blocks?.find((field) => field.key === key)?.value;
    if (typeof blockValue === "string" && blockValue.trim()) {
      return blockValue.trim();
    }
  }
  return "";
}

function buildDraftContextFallback(patientId: string, draft: DocumentDraft | null | undefined, bedNoHint?: string): PatientContext | null {
  if (!draft) {
    return null;
  }
  const patientName = normalizePersonName(getDraftFieldText(draft, ["patient_name", "full_name", "name"]), "");
  const bedNo = normalizeBedNo(bedNoHint || getDraftFieldText(draft, ["bed_no", "bedNo", "bed"]));
  if (!patientName && !bedNo) {
    return null;
  }
  return {
    patient_id: patientId,
    patient_name: patientName || undefined,
    bed_no: bedNo || undefined,
    encounter_id: getDraftFieldText(draft, ["encounter_id"]) || draft.encounter_id || undefined,
    diagnoses: [],
    risk_tags: [],
    pending_tasks: [],
    latest_observations: [],
    latest_document_sync: draft.updated_at,
    latest_document_status: draft.status,
    latest_document_type: draft.document_type,
    latest_document_excerpt: draft.draft_text,
    latest_document_updated_at: draft.updated_at,
  };
}

function buildPatientFallbackFromDraft(patientId: string, draft: DocumentDraft | null | undefined): Patient | null {
  if (!draft) {
    return null;
  }
  const fullName = normalizePersonName(getDraftFieldText(draft, ["patient_name", "full_name", "name"]), "");
  if (!fullName) {
    return null;
  }
  return {
    id: patientId,
    mrn: getDraftFieldText(draft, ["mrn"]) || "",
    inpatient_no: getDraftFieldText(draft, ["inpatient_no"]) || undefined,
    full_name: fullName,
    current_status: "active",
  };
}

export function DocumentEditorScreen({ navigation, route }: Props) {
  const { draftId, patientId: routePatientId, bedNo: routeBedNo, initialDraft } = route.params;
  const { width } = useWindowDimensions();
  const user = useAppStore((state) => state.user);
  const departmentId = useAppStore((state) => state.selectedDepartmentId);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [context, setContext] = useState<PatientContext | null>(null);
  const [draft, setDraft] = useState<DocumentDraft | null>(null);
  const [draftText, setDraftText] = useState("");
  const [structuredFields, setStructuredFields] = useState<DocumentStructuredFields>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resolvedPatientId, setResolvedPatientId] = useState(routePatientId);

  const wideLayout = width >= 1680;
  const compactLayout = width < 1080;
  const patientId = resolvedPatientId || routePatientId;

  useEffect(() => {
    navigation.setOptions({ title: "标准文书编辑" });
  }, [navigation]);

  useEffect(() => {
    setResolvedPatientId(routePatientId);
  }, [routeBedNo, routePatientId]);

  useEffect(() => {
    let active = true;

    const load = async () => {
      const seededDraft = initialDraft && initialDraft.id === draftId ? initialDraft : null;
      const seededDraftNeedsHydration =
        seededDraft &&
        (!getEditableBlocks(seededDraft).length ||
          !Array.isArray(getStructuredFields(seededDraft).standard_form?.sheet_columns) ||
          !getStructuredFields(seededDraft).standard_form?.sheet_columns?.length);
      if (seededDraft && active) {
        applyDraftState(seededDraft, setDraft, setDraftText, setStructuredFields);
        setLoading(Boolean(seededDraftNeedsHydration));
      } else {
        setLoading(true);
      }
      setError("");
      try {
        let targetPatientId = routePatientId;
        let seededContext: PatientContext | null = null;
        const targetBedNo = normalizeBedNo(
          routeBedNo ||
            initialDraft?.structured_fields?.bed_no ||
            initialDraft?.structured_fields?.editable_blocks?.find((item) => item.key === "bed_no")?.value ||
            ""
        );
        if (targetBedNo) {
          try {
            const bedContext = await api.getBedContext(targetBedNo, {
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
        if (active) {
          setResolvedPatientId(targetPatientId);
        }
        const [patientResult, contextResult, historyResult, inboxResult] = await Promise.allSettled([
          api.getPatient(targetPatientId),
          seededContext ? Promise.resolve(seededContext) : api.getPatientContext(targetPatientId, user?.id),
          api.listDrafts(targetPatientId, user?.id),
          user?.id ? api.getDocumentInbox(user.id, { patientId: targetPatientId, limit: 100 }) : Promise.resolve([]),
        ]);

        if (patientResult.status === "fulfilled" && active) {
          setPatient(patientResult.value);
        } else if (active) {
          const seededDraftContext = buildDraftContextFallback(targetPatientId, seededDraft, targetBedNo || routeBedNo);
          const fallbackPatient =
            (contextResult.status === "fulfilled" ? buildPatientFallback(targetPatientId, contextResult.value) : null) ||
            buildPatientFallback(targetPatientId, seededContext) ||
            buildPatientFallback(targetPatientId, seededDraftContext) ||
            buildPatientFallbackFromDraft(targetPatientId, seededDraft);
          setPatient(fallbackPatient);
          if (!fallbackPatient) {
            setError(getApiErrorMessage(patientResult.status === "rejected" ? patientResult.reason : null, "患者基础档案未返回，继续展示当前草稿。"));
          }
        }
        if (contextResult.status === "fulfilled" && active) {
          setContext(contextResult.value);
        } else if (active) {
          setContext(buildDraftContextFallback(targetPatientId, seededDraft, targetBedNo || routeBedNo));
        }

        const historyRows = historyResult.status === "fulfilled" ? historyResult.value : [];
        const inboxRows = inboxResult.status === "fulfilled" ? inboxResult.value : [];
        const mergedDrafts = [...historyRows, ...inboxRows].reduce<DocumentDraft[]>((items, item) => {
          const hitIndex = items.findIndex((current) => current.id === item.id);
          if (hitIndex === -1) {
            return [...items, item];
          }
          if (getDraftQualityScore(item) >= getDraftQualityScore(items[hitIndex])) {
            const next = [...items];
            next[hitIndex] = item;
            return next;
          }
          return items;
        }, []);
        const matchedDraft = mergedDrafts.find((item) => item.id === draftId) || null;
        const preferredDraft = pickPreferredDraft([seededDraft, matchedDraft]);

        if (!preferredDraft) {
          throw new Error("draft_not_found");
        }

        const draftPatientId = String(getDraftFieldText(preferredDraft, ["patient_id"]) || preferredDraft.patient_id || targetPatientId || "").trim();
        const draftContextFallback = buildDraftContextFallback(draftPatientId || targetPatientId, preferredDraft, targetBedNo || routeBedNo);
        if (active && draftPatientId && draftPatientId !== targetPatientId) {
          setResolvedPatientId(draftPatientId);
        }
        if (active && contextResult.status !== "fulfilled" && draftContextFallback) {
          setContext(draftContextFallback);
        }
        if (active && patientResult.status !== "fulfilled") {
          const fallbackPatient =
            buildPatientFallback(draftPatientId || targetPatientId, draftContextFallback) ||
            buildPatientFallbackFromDraft(draftPatientId || targetPatientId, preferredDraft);
          if (fallbackPatient) {
            setPatient(fallbackPatient);
          }
        }

        let nextDraft = preferredDraft;
        if (!getEditableBlocks(nextDraft).length || !nextDraft.structured_fields?.standard_form?.sheet_columns?.length) {
          try {
            const standardForm = await api.getStandardForm(resolveStandardFormLookupKey(nextDraft));
            nextDraft = hydrateDraftForEditing(nextDraft, {
              standardForm,
              patient: patientResult.status === "fulfilled" ? patientResult.value : null,
              context: contextResult.status === "fulfilled" ? contextResult.value : null,
            });
          } catch {
            nextDraft = preferredDraft;
          }
        }

        if (active) {
          applyDraftState(nextDraft, setDraft, setDraftText, setStructuredFields);
        }
      } catch (err) {
        if (active) {
          setError(getApiErrorMessage(err, "未能打开这份文书，请返回上一页后重新进入。"));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    load();
    return () => {
      active = false;
    };
  }, [departmentId, draftId, initialDraft, routeBedNo, routePatientId, user?.id]);

  const editableBlocks = useMemo(() => {
    if (!draft) {
      return [];
    }
    return getEditableBlocks({
      ...draft,
      structured_fields: structuredFields,
    });
  }, [draft, structuredFields]);

  const missingCount = Number(structuredFields.field_summary?.missing || editableBlocks.filter((item) => item.required && !item.value.trim()).length);
  const filledCount = Number(structuredFields.field_summary?.filled || editableBlocks.filter((item) => item.value.trim()).length);
  const totalCount = Number(structuredFields.field_summary?.total || editableBlocks.length);
  const resolvedBedNo = normalizeBedNo(
    context?.bed_no ||
      editableBlocks.find((item) => item.key === "bed_no")?.value?.trim() ||
      draft?.structured_fields?.editable_blocks?.find((item) => item.key === "bed_no")?.value?.trim() ||
      ""
  );
  const draftPatientName = draft ? normalizePersonName(getDraftFieldText(draft, ["patient_name", "full_name", "name"]), "") : "";
  const draftMrn = draft ? getDraftFieldText(draft, ["mrn"]) : "";
  const standardFormName =
    draft?.structured_fields?.template_name || draft?.structured_fields?.standard_form?.name || structuredFields.standard_form?.name || "标准护理文书模板";
  const sourceRefs = Array.isArray(draft?.structured_fields?.template_source_refs)
    ? draft?.structured_fields?.template_source_refs
    : Array.isArray(draft?.structured_fields?.standard_form?.source_refs)
    ? draft?.structured_fields?.standard_form?.source_refs
    : Array.isArray(structuredFields.standard_form?.source_refs)
    ? structuredFields.standard_form?.source_refs
    : [];
  const risk = buildClinicalRiskBadge(context || {});
  const pageTitle = draft
    ? `${normalizePersonName(patient?.full_name || context?.patient_name || draftPatientName, "患者")} · ${getDocumentTypeLabel(
        draft.document_type
      )}`
    : "标准文书编辑";
  const pageSubtitle = [
    resolvedBedNo ? formatBedLabel(resolvedBedNo) : "",
    patient?.mrn || draftMrn ? `病案号 ${patient?.mrn || draftMrn}` : "",
    standardFormName,
  ]
    .filter(Boolean)
    .join(" · ");

  const saveDraft = async () => {
    if (!draft) {
      return;
    }
    try {
      setBusy(true);
      setError("");
      const bindingFields: DocumentStructuredFields = {
        ...structuredFields,
        patient_id: patientId,
        encounter_id: context?.encounter_id || draft.encounter_id || undefined,
        bed_no: resolvedBedNo || routeBedNo || undefined,
        patient_name: patient?.full_name || context?.patient_name || draftPatientName || undefined,
        full_name: patient?.full_name || context?.patient_name || draftPatientName || undefined,
        mrn: patient?.mrn || draftMrn || undefined,
        inpatient_no: patient?.inpatient_no || undefined,
        requested_by: user?.id || undefined,
      };
      const saved = await api.editDraft(draft.id, {
        draftText,
        editedBy: user?.id,
        structuredFields: bindingFields,
      });
      applyDraftState(saved, setDraft, setDraftText, setStructuredFields);
      navigation.setParams({ initialDraft: saved });
    } catch (err) {
      setError(getApiErrorMessage(err, "文书保存失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const reviewDraft = async () => {
    if (!draft) {
      return;
    }
    try {
      setBusy(true);
      setError("");
      const reviewed = await api.reviewDraft(draft.id, user?.id || "u_nurse_01");
      applyDraftState(reviewed, setDraft, setDraftText, setStructuredFields);
    } catch (err) {
      setError(getApiErrorMessage(err, "文书审核失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const submitDraft = async () => {
    if (!draft) {
      return;
    }
    try {
      setBusy(true);
      setError("");
      await api.submitDraft(draft.id, user?.id || "u_nurse_01");
      navigation.goBack();
    } catch (err) {
      setError(getApiErrorMessage(err, "文书归档失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (!draft) {
    return (
      <ScreenShell title="标准文书编辑" subtitle="未找到目标文书">
        <InfoBanner title="文书打开失败" description={error || "这份文书可能已经被归档或当前页面缓存已过期。"} tone="danger" />
        <ActionButton label="返回上一页" onPress={() => navigation.goBack()} variant="secondary" />
      </ScreenShell>
    );
  }

  return (
    <ScreenShell
      title={pageTitle}
      subtitle={pageSubtitle}
      scroll={compactLayout}
      rightNode={<StatusPill text={getStatusLabel(draft.status)} tone={draft.status === "reviewed" ? "info" : "warning"} />}
    >
      {error ? <InfoBanner title="编辑页处理失败" description={error} tone="danger" /> : null}

      <View style={[styles.pageBody, compactLayout && styles.pageBodyCompact]}>
        {compactLayout ? (
          <>
            <View style={[styles.actionStrip, styles.actionStripCompact]}>
              <ActionButton label="返回上一页" onPress={() => navigation.goBack()} variant="secondary" disabled={busy} style={styles.actionButton} />
              {draft.status === "draft" ? (
                <ActionButton
                  label={missingCount > 0 ? "先补字段再审核" : "提交审核"}
                  onPress={reviewDraft}
                  disabled={busy || missingCount > 0}
                  style={styles.actionButton}
                />
              ) : null}
              {draft.status === "reviewed" ? (
                <ActionButton
                  label={missingCount > 0 ? "先补字段再归档" : "归档入病例"}
                  onPress={submitDraft}
                  disabled={busy || missingCount > 0}
                  style={styles.actionButton}
                />
              ) : null}
            </View>
            <View style={[styles.editorWrap, styles.editorWrapCompact]}>
              <DocumentStructuredEditor
                key={draft.id}
                draft={draft}
                draftText={draftText}
                structuredFields={structuredFields}
                busy={busy}
                presentation="inline"
                onDraftTextChange={setDraftText}
                onStructuredFieldsChange={setStructuredFields}
                onCancel={() => navigation.goBack()}
                onSave={saveDraft}
              />
            </View>
          </>
        ) : (
          <>
        <View style={[styles.summaryGrid, wideLayout && styles.summaryGridWide]}>
          <SurfaceCard style={styles.summaryCard}>
            <Text style={styles.cardTitle}>患者与风险</Text>
            <SummaryItem label="床位" value={resolvedBedNo ? formatBedLabel(resolvedBedNo) : "-"} />
            <SummaryItem label="患者" value={normalizePersonName(patient?.full_name || context?.patient_name, "-")} />
            <SummaryItem label="病案号" value={patient?.mrn || "-"} />
            <SummaryItem label="风险层级" value={risk.label} />
            <Text style={styles.cardHint}>{risk.reason}</Text>
          </SurfaceCard>

          <SurfaceCard style={styles.summaryCard}>
            <Text style={styles.cardTitle}>标准模板</Text>
            <SummaryItem label="模板" value={standardFormName} />
            <SummaryItem label="文书类型" value={getDocumentTypeLabel(draft.document_type)} />
            <SummaryItem label="归档提示" value={formatArchiveHint(draft)} />
            {sourceRefs.length ? <Text style={styles.cardHint}>模板来源：{sourceRefs.join(" / ")}</Text> : null}
          </SurfaceCard>

          <SurfaceCard style={styles.summaryCard}>
            <Text style={styles.cardTitle}>填写进度</Text>
            <SummaryItem label="可编辑字段" value={String(totalCount)} />
            <SummaryItem label="已填写" value={String(filledCount)} />
            <SummaryItem label="待补" value={String(missingCount)} />
          </SurfaceCard>
        </View>

        <View style={[styles.workbenchGrid, wideLayout && styles.workbenchGridWide]}>
          <WorkbenchCard title="Word 正文" description="适合整理病情观察、护理措施、效果评价和交班表述，支持标题、加粗、列表和段落模板。" />
          <WorkbenchCard title="Excel 表格" description="适合逐格补录生命体征、出入量、时间点、签名和半结构化栏位，避免漏填。" />
          <WorkbenchCard title="结构化字段" description="直接把信息填到标准模板对应栏目，缺失项会持续高亮，便于审核前逐项核对。" />
          <WorkbenchCard title="归档预览" description="提交前先看最终归档正文和检查清单，确认患者标识、时间逻辑和人工确认结果一致。" />
        </View>

        <View style={[styles.actionStrip, compactLayout && styles.actionStripCompact]}>
          <ActionButton label="返回上一页" onPress={() => navigation.goBack()} variant="secondary" disabled={busy} style={styles.actionButton} />
          {draft.status === "draft" ? (
            <ActionButton
              label={missingCount > 0 ? "先补字段再审核" : "提交审核"}
              onPress={reviewDraft}
              disabled={busy || missingCount > 0}
              style={styles.actionButton}
            />
          ) : null}
          {draft.status === "reviewed" ? (
            <ActionButton
              label={missingCount > 0 ? "先补字段再归档" : "归档入病例"}
              onPress={submitDraft}
              disabled={busy || missingCount > 0}
              style={styles.actionButton}
            />
          ) : null}
        </View>

        <View style={[styles.editorWrap, compactLayout && styles.editorWrapCompact]}>
          <DocumentStructuredEditor
            key={draft.id}
            draft={draft}
            draftText={draftText}
            structuredFields={structuredFields}
            busy={busy}
            presentation="inline"
            onDraftTextChange={setDraftText}
            onStructuredFieldsChange={setStructuredFields}
            onCancel={() => navigation.goBack()}
            onSave={saveDraft}
          />
        </View>
          </>
        )}
      </View>
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
  pageBody: {
    flex: 1,
    minHeight: 0,
    gap: 12,
  },
  pageBodyCompact: {
    gap: 12,
  },
  summaryGrid: {
    gap: 12,
  },
  summaryGridWide: {
    flexDirection: "row",
    alignItems: "stretch",
  },
  workbenchGrid: {
    gap: 12,
  },
  workbenchGridWide: {
    flexDirection: "row",
    flexWrap: "wrap",
  },
  summaryCard: {
    flex: 1,
    gap: 10,
  },
  compactSummaryCard: {
    gap: 8,
  },
  workbenchCard: {
    flex: 1,
    minWidth: 220,
    gap: 8,
  },
  cardTitle: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "800",
  },
  workbenchTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  workbenchText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 19,
  },
  summaryItem: {
    gap: 2,
  },
  summaryLabel: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  summaryValue: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20,
    fontWeight: "600",
  },
  cardHint: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  actionStrip: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  actionStripCompact: {
    gap: 8,
  },
  actionButton: {
    minWidth: 138,
  },
  editorWrap: {
    flex: 1,
    minHeight: 0,
  },
  editorWrapCompact: {
    flex: 0,
  },
});
