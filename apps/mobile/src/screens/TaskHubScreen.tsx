import React, { useEffect, useMemo, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import { api, getApiErrorMessage } from "../api/endpoints";
import { DocumentStructuredEditor } from "../components/DocumentStructuredEditor";
import { AppGlyph } from "../components/AppGlyph";
import { ActionButton, InfoBanner, ScreenShell, StatusPill, SurfaceCard } from "../components/ui";
import type { RootStackParamList } from "../navigation/RootNavigator";
import { useAppStore } from "../store/appStore";
import { colors } from "../theme";
import type {
  AgentQueueTask,
  AgentRunRecord,
  BedOverview,
  CollabAccount,
  DirectSession,
  DocumentDraft,
  DocumentStructuredFields,
  StandardFormBundle,
  DocumentTemplate,
} from "../types";
import { buildAssistantPreviewText } from "../utils/aiAssistantText";
import { buildClinicalRiskBadge } from "../utils/clinicalRisk";
import { formatArchiveHint, getDraftArchiveIdentity, getStructuredFields, hydrateDraftForEditing } from "../utils/documentDraft";
import { loadChatSessions, type ChatSessionRecord } from "../utils/chatSessionStore";
import {
  getDocumentTypeLabel,
  getRoleLabel,
  getSourceTypeLabel,
  getStatusLabel,
  getWorkflowTypeLabel,
} from "../utils/displayText";
import { formatBedLabel, normalizeBedNo, normalizePersonName } from "../utils/displayValue";
import { AI_AGENT_CHAT_SESSION_ID, AI_AGENT_CHAT_TITLE } from "../utils/messageThreads";
import { buildNursingLevelTone } from "../utils/nursingLevel";
import { formatAiText } from "../utils/text";

type HubView = "messages" | "contacts" | "inbox";
type InboxTab = "approvals" | "drafts" | "templates";

function tone(status: string) {
  if (status === "completed" || status === "submitted" || status === "reviewed") return "success" as const;
  if (status === "failed" || status === "cancelled") return "danger" as const;
  if (status === "waiting_approval" || status === "draft") return "warning" as const;
  return "info" as const;
}

function getDraftScore(item: DocumentDraft) {
  const structured = getStructuredFields(item);
  const editableCount = Array.isArray(structured.editable_blocks) ? structured.editable_blocks.length : 0;
  const sheetColumns = Array.isArray(structured.standard_form?.sheet_columns) ? structured.standard_form?.sheet_columns.length : 0;
  const updatedAt = Date.parse(String(item.updated_at || item.created_at || ""));
  const updatedWeight = Number.isFinite(updatedAt) ? Math.floor(updatedAt / 1000) : 0;
  return editableCount * 100000 + sheetColumns * 1000 + updatedWeight;
}

function mergeDraftRows(rows: DocumentDraft[]) {
  const merged = new Map<string, DocumentDraft>();
  rows.forEach((item) => {
    const current = merged.get(item.id);
    if (!current || getDraftScore(item) >= getDraftScore(current)) {
      merged.set(item.id, item);
    }
  });
  return Array.from(merged.values());
}

function getArchiveIdentityScore(identity: { bedNo?: string; patientName?: string; patientId?: string; patientIdHint?: string }) {
  return (identity.bedNo ? 100 : 0) + (identity.patientName ? 10 : 0) + (identity.patientId ? 5 : 0) + (identity.patientIdHint ? 1 : 0);
}

function resolveCanonicalDraftIdentity(
  draft: DocumentDraft,
  meta: { bedNo?: string; patientName?: string } | undefined,
  wardBedByNo: Map<string, BedOverview>,
  wardBedByPatientId: Map<string, BedOverview>,
  wardBedsByPatientName: Map<string, BedOverview[]>
) {
  const identity = getDraftArchiveIdentity(draft, meta);
  const draftPatientId = String(identity.patientId || draft.patient_id || "").trim();
  let wardBed = identity.bedNo ? wardBedByNo.get(normalizeBedNo(identity.bedNo)) : undefined;
  if (!wardBed && draftPatientId) {
    wardBed = wardBedByPatientId.get(draftPatientId);
  }
  const fallbackPatientName = normalizePersonName(meta?.patientName || identity.patientName, "");
  if (!wardBed && fallbackPatientName) {
    const matchedBeds = wardBedsByPatientName.get(fallbackPatientName) || [];
    if (matchedBeds.length === 1) {
      wardBed = matchedBeds[0];
    }
  }

  const canonicalBedNo = String(wardBed?.bed_no || identity.bedNo || "").trim();
  const canonicalPatientName = normalizePersonName(wardBed?.patient_name || fallbackPatientName, "");
  const canonicalPatientId = wardBed?.current_patient_id || identity.patientId || draft.patient_id;

  return {
    ...identity,
    groupKey: canonicalBedNo ? `bed:${canonicalBedNo}` : canonicalPatientId ? `patient:${canonicalPatientId}` : identity.groupKey,
    title: canonicalBedNo ? `${canonicalBedNo}床${canonicalPatientName ? ` · ${canonicalPatientName}` : ""}` : identity.title,
    subtitle: identity.patientIdHint ? `病历索引：${identity.patientIdHint}` : identity.subtitle,
    bedNo: canonicalBedNo || identity.bedNo,
    patientName: canonicalPatientName || identity.patientName,
    patientId: canonicalPatientId || undefined,
  };
}

function resolveDraftNavigationTarget(
  draft: DocumentDraft,
  meta: { bedNo?: string; patientName?: string } | undefined,
  wardBedByNo: Map<string, BedOverview>,
  wardBedByPatientId: Map<string, BedOverview>,
  wardBedsByPatientName: Map<string, BedOverview[]>
) {
  const identity = resolveCanonicalDraftIdentity(draft, meta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName);
  return {
    patientId: identity.patientId || draft.patient_id,
    bedNo: identity.bedNo || normalizeBedNo(identity.title),
  };
}

function findLiveWardBedForDraft(
  draft: DocumentDraft,
  meta: { bedNo?: string; patientName?: string } | undefined,
  wardBedByNo: Map<string, BedOverview>,
  wardBedByPatientId: Map<string, BedOverview>,
  wardBedsByPatientName: Map<string, BedOverview[]>
) {
  const identity = getDraftArchiveIdentity(draft, meta);
  const draftPatientId = String(identity.patientId || draft.patient_id || "").trim();
  const normalizedBedNo = normalizeBedNo(identity.bedNo);
  if (normalizedBedNo) {
    return wardBedByNo.get(normalizedBedNo);
  }
  if (draftPatientId) {
    const patientBed = wardBedByPatientId.get(draftPatientId);
    if (patientBed) {
      return patientBed;
    }
  }
  const patientName = normalizePersonName(identity.patientName, "");
  if (patientName) {
    const matchedBeds = wardBedsByPatientName.get(patientName) || [];
    if (matchedBeds.length === 1) {
      return matchedBeds[0];
    }
  }
  return undefined;
}

function isDraftObsoleteForActiveBed(
  draft: DocumentDraft,
  meta: { bedNo?: string; patientName?: string } | undefined,
  wardBedByNo: Map<string, BedOverview>,
  wardBedByPatientId: Map<string, BedOverview>,
  wardBedsByPatientName: Map<string, BedOverview[]>
) {
  const draftPatientId = String(draft.patient_id || "").trim();
  if (!draftPatientId) {
    return false;
  }
  const liveWardBed = findLiveWardBedForDraft(draft, meta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName);
  const currentPatientId = String(liveWardBed?.current_patient_id || "").trim();
  if (!liveWardBed?.bed_no || !currentPatientId) {
    return false;
  }
  return currentPatientId !== draftPatientId;
}

function getDraftGroupMergeKey(group: { key: string; title: string; bedNo?: string; patientId?: string }) {
  if (group.bedNo) {
    return `bed:${normalizeBedNo(group.bedNo)}`;
  }
  if (group.patientId) {
    return `patient:${group.patientId}`;
  }
  const titleBedNo = normalizeBedNo(group.title);
  if (titleBedNo) {
    return `bed:${titleBedNo}`;
  }
  const titleName = normalizePersonName(
    String(group.title || "")
      .replace(/^[^A-Za-z0-9\u4e00-\u9fff]*/, "")
      .replace(/^[A-Za-z]?\d{1,3}\s*床?/, "")
      .replace(/^床\s*/, "")
      .replace(/^[\s·/|路-]+/, ""),
    ""
  );
  if (titleName) {
    return `name:${titleName}`;
  }
  return group.key;
}

function buildCanonicalDraftIdentity(
  draft: DocumentDraft,
  meta: { bedNo?: string; patientName?: string } | undefined,
  wardBedByNo: Map<string, BedOverview>,
  wardBedByPatientId: Map<string, BedOverview>
) {
  const identity = getDraftArchiveIdentity(draft, meta);
  const draftPatientId = String(identity.patientId || draft.patient_id || "").trim();
  const wardBed =
    (identity.bedNo ? wardBedByNo.get(normalizeBedNo(identity.bedNo)) : undefined) ||
    (draftPatientId ? wardBedByPatientId.get(draftPatientId) : undefined);
  const canonicalBedNo = String(wardBed?.bed_no || identity.bedNo || "").trim();
  const canonicalPatientName = normalizePersonName(wardBed?.patient_name || identity.patientName, "");
  const canonicalPatientId = wardBed?.current_patient_id || identity.patientId || draft.patient_id;
  const title = canonicalBedNo ? `${canonicalBedNo}床${canonicalPatientName ? ` · ${canonicalPatientName}` : ""}` : identity.title;
  return {
    ...identity,
    groupKey: canonicalBedNo ? `bed:${canonicalBedNo}` : canonicalPatientId ? `patient:${canonicalPatientId}` : identity.groupKey,
    title,
    subtitle: identity.patientIdHint ? `病历索引：${identity.patientIdHint}` : identity.subtitle,
    bedNo: canonicalBedNo || identity.bedNo,
    patientName: canonicalPatientName || identity.patientName,
    patientId: canonicalPatientId || undefined,
  };
}

function formatConversationTime(value?: string) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function sessionPreview(session: DirectSession) {
  const preview = session.latest_message?.content?.trim();
  if (preview) {
    return preview;
  }
  if (session.patient_id) {
    return `已关联患者 ${session.patient_id}`;
  }
  return "点击开始协作";
}

function ListRow(props: {
  title: string;
  subtitle: string;
  subtitleLines?: number;
  leading: string;
  trailing?: string;
  badgeText?: string;
  unreadCount?: number;
  onPress?: () => void;
}) {
  const content = (
    <View style={styles.listRow}>
      <View style={styles.rowLeading}>
        <Text style={styles.rowLeadingText}>{props.leading}</Text>
      </View>
      <View style={styles.rowBody}>
        <View style={styles.rowHead}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {props.title}
          </Text>
          {props.trailing ? <Text style={styles.rowTrailing}>{props.trailing}</Text> : null}
        </View>
        <View style={styles.rowSubLine}>
          <Text style={styles.rowSubtitle} numberOfLines={props.subtitleLines || 2}>
            {props.subtitle}
          </Text>
          {props.unreadCount ? (
            <View style={styles.unreadBadge}>
              <Text style={styles.unreadText}>{props.unreadCount}</Text>
            </View>
          ) : null}
        </View>
        {props.badgeText ? (
          <View style={styles.inlineBadge}>
            <Text style={styles.inlineBadgeText}>{props.badgeText}</Text>
          </View>
        ) : null}
      </View>
      <AppGlyph name="chevron" color={colors.subText} />
    </View>
  );

  if (props.onPress) {
    return <Pressable onPress={props.onPress}>{content}</Pressable>;
  }
  return content;
}

function PanelSection({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <View style={styles.panelSection}>
      <View style={styles.panelSectionHead}>
        <Text style={styles.panelSectionTitle}>{title}</Text>
        {action}
      </View>
      <View style={styles.flatPanel}>{children}</View>
    </View>
  );
}

function PatientArchiveRow(props: {
  title: string;
  subtitle: string;
  count: number;
  onPress: () => void;
}) {
  return (
    <Pressable style={styles.archiveEntryRow} onPress={props.onPress}>
      <View style={styles.archiveEntryLeading}>
        <AppGlyph name="document" color={colors.primary} />
      </View>
      <View style={styles.archiveEntryBody}>
        <Text style={styles.archiveEntryTitle}>{props.title}</Text>
        <Text style={styles.archiveEntrySubtitle}>{props.subtitle}</Text>
      </View>
      <View style={styles.archiveEntryRight}>
        <Text style={styles.archiveEntryCount}>{props.count}</Text>
        <AppGlyph name="chevron" color={colors.subText} />
      </View>
    </Pressable>
  );
}

function PickerChip(props: { active: boolean; label: string; note?: string; onPress: () => void }) {
  return (
    <Pressable style={[styles.pickerChip, props.active && styles.pickerChipActive]} onPress={props.onPress}>
      <Text style={[styles.pickerChipLabel, props.active && styles.pickerChipLabelActive]}>{props.label}</Text>
      {props.note ? <Text style={[styles.pickerChipNote, props.active && styles.pickerChipNoteActive]}>{props.note}</Text> : null}
    </Pressable>
  );
}

export function TaskHubScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { width: screenWidth } = useWindowDimensions();
  const compactLayout = screenWidth < 960;
  const visualCarouselRef = useRef<ScrollView | null>(null);
  const user = useAppStore((state) => state.user);
  const departmentId = useAppStore((state) => state.selectedDepartmentId);
  const [view, setView] = useState<HubView>("messages");
  const [inboxTab, setInboxTab] = useState<InboxTab>("approvals");
  const [searchText, setSearchText] = useState("");
  const [friendAccount, setFriendAccount] = useState("");
  const [queueTasks, setQueueTasks] = useState<AgentQueueTask[]>([]);
  const [runs, setRuns] = useState<AgentRunRecord[]>([]);
  const [drafts, setDrafts] = useState<DocumentDraft[]>([]);
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [standardForms, setStandardForms] = useState<StandardFormBundle[]>([]);
  const [contacts, setContacts] = useState<CollabAccount[]>([]);
  const [directSessions, setDirectSessions] = useState<DirectSession[]>([]);
  const [aiSession, setAiSession] = useState<ChatSessionRecord | null>(null);
  const [wardBeds, setWardBeds] = useState<BedOverview[]>([]);
  const [patientArchiveMeta, setPatientArchiveMeta] = useState<Record<string, { bedNo?: string; patientName?: string }>>({});
  const [editingDraft, setEditingDraft] = useState<DocumentDraft | null>(null);
  const [draftText, setDraftText] = useState("");
  const [editingStructuredFields, setEditingStructuredFields] = useState<DocumentStructuredFields>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [visualCardWidth, setVisualCardWidth] = useState(0);
  const [activeVisualCard, setActiveVisualCard] = useState(0);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [selectedTemplatePatientId, setSelectedTemplatePatientId] = useState("");
  const [templateLaunchNotes, setTemplateLaunchNotes] = useState("");
  const hasLoadedOnceRef = useRef(false);
  const lastRefreshRef = useRef(0);

  const load = async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent && hasLoadedOnceRef.current);
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const [queueResult, runsResult, templatesResult, standardFormsResult, draftsResult, contactsResult, sessionsResult, storedSessionsResult, wardBedsResult] =
        await Promise.allSettled([
        api.listAgentQueueTasks({ limit: 12 }),
        api.listAgentRuns({ limit: 12 }),
        api.listDocumentTemplates(),
        api.listStandardForms(),
        user?.id ? api.getDocumentInbox(user.id, { limit: 40 }) : Promise.resolve([]),
        user?.id ? api.getCollabContacts(user.id) : Promise.resolve({ user_id: "", contacts: [] }),
        user?.id ? api.listDirectSessions(user.id, 60) : Promise.resolve([]),
        loadChatSessions(),
        api.getWardBeds(departmentId),
      ]);

      const nextQueue = queueResult.status === "fulfilled" ? queueResult.value : [];
      const nextRuns = runsResult.status === "fulfilled" ? runsResult.value : [];
      const nextTemplates = templatesResult.status === "fulfilled" ? templatesResult.value : [];
      const nextStandardForms = standardFormsResult.status === "fulfilled" ? standardFormsResult.value : [];
      const nextDrafts = draftsResult.status === "fulfilled" ? draftsResult.value : [];
      const nextContacts = contactsResult.status === "fulfilled" ? contactsResult.value : { user_id: "", contacts: [] };
      const nextSessions = sessionsResult.status === "fulfilled" ? sessionsResult.value : [];
      const storedSessions = storedSessionsResult.status === "fulfilled" ? storedSessionsResult.value : [];
      const nextWardBeds = wardBedsResult.status === "fulfilled" ? wardBedsResult.value : [];

      setQueueTasks(nextQueue);
      setRuns(nextRuns);
      setTemplates(nextTemplates);
      setStandardForms(nextStandardForms);
      setDrafts(nextDrafts);
      setContacts(nextContacts.contacts || []);
      setDirectSessions(nextSessions);
      setAiSession(
        storedSessions.find((item) => item.id === AI_AGENT_CHAT_SESSION_ID) ||
          storedSessions.find((item) => item.title === AI_AGENT_CHAT_TITLE) ||
          storedSessions[0] ||
          null
      );
      setWardBeds(Array.isArray(nextWardBeds) ? nextWardBeds : []);
      const coreRejected = [queueResult, runsResult, templatesResult, standardFormsResult, draftsResult].filter(
        (item) => item.status === "rejected"
      );
      if (coreRejected.length >= 3) {
        const firstRejected = coreRejected[0] as PromiseRejectedResult | undefined;
        setError(getApiErrorMessage(firstRejected?.reason, "协作中心核心数据加载失败，请检查网关与后端服务。"));
      }

      if (coreRejected.length < 3) {
        setError("");
      }

      const wardArchiveMeta = Object.fromEntries(
        (Array.isArray(nextWardBeds) ? nextWardBeds : [])
          .filter((item) => item.current_patient_id)
          .map((item) => [
            item.current_patient_id!,
            {
              bedNo: item.bed_no,
              patientName: item.patient_name,
            },
          ])
      );

      const patientIds = Array.from(new Set(nextDrafts.map((item) => item.patient_id).filter(Boolean)));
      if (patientIds.length) {
        const contextRows = await Promise.all(
          patientIds.map(async (id) => {
            try {
              const ctx = await api.getPatientContext(id, user?.id);
              return [id, { bedNo: ctx.bed_no, patientName: ctx.patient_name }] as const;
            } catch {
              try {
                const patient = await api.getPatient(id);
                return [id, { bedNo: undefined, patientName: patient.full_name }] as const;
              } catch {
                return [id, { bedNo: undefined, patientName: undefined }] as const;
              }
            }
          })
        );
        const mergedMeta: Record<string, { bedNo?: string; patientName?: string }> = { ...wardArchiveMeta };
        contextRows.forEach(([id, meta]) => {
          const fallback = mergedMeta[id] || {};
          mergedMeta[id] = {
            bedNo: meta.bedNo || fallback.bedNo,
            patientName: meta.patientName || fallback.patientName,
          };
        });
        setPatientArchiveMeta(mergedMeta);
      } else {
        setPatientArchiveMeta(wardArchiveMeta);
      }
    } catch (err) {
      setError(getApiErrorMessage(err, "协作中心加载失败，请稍后重试。"));
    } finally {
      if (!silent) {
        setLoading(false);
      }
      hasLoadedOnceRef.current = true;
      lastRefreshRef.current = Date.now();
    }
  };

  useEffect(() => {
    void load();
  }, [user?.id, departmentId]);

  useFocusEffect(
    React.useCallback(() => {
      if (!hasLoadedOnceRef.current) {
        return;
      }
      if (busy || editingDraft) {
        return;
      }
      if (Date.now() - lastRefreshRef.current < 15000) {
        return;
      }
      void load({ silent: true });
    }, [busy, departmentId, editingDraft, user?.id])
  );

  const openEditor = (draft: DocumentDraft) => {
    setView("inbox");
    setInboxTab("drafts");
    const meta = patientArchiveMeta[draft.patient_id] || {};
    const target = resolveDraftNavigationTarget(draft, meta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName);
    navigation.push("DocumentEditor", {
      patientId: target.patientId,
      bedNo: target.bedNo,
      draftId: draft.id,
      initialDraft: draft,
    });
  };

  const resetEditor = () => {
    setEditingDraft(null);
    setDraftText("");
    setEditingStructuredFields({});
  };

  const importTemplate = async () => {
    try {
      setBusy(true);
      const picked = await DocumentPicker.getDocumentAsync({
        multiple: false,
        copyToCacheDirectory: true,
        type: ["text/*", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
      });
      if (picked.canceled || !picked.assets.length) {
        return;
      }
      const asset = picked.assets[0];
      const base64 = await FileSystem.readAsStringAsync(asset.uri, {
        encoding: FileSystem.EncodingType.Base64,
      });
      await api.importDocumentTemplate({
        name: asset.name,
        templateBase64: base64,
        fileName: asset.name,
        mimeType: asset.mimeType || undefined,
      });
      await load({ silent: true });
      setView("inbox");
      setInboxTab("templates");
    } catch (err) {
      setError(getApiErrorMessage(err, "模板导入失败，请换一个 txt 或 docx 后再试。"));
    } finally {
      setBusy(false);
    }
  };

  const createDraftFromTemplate = async () => {
    if (!selectedTemplate) {
      setError("请先选择一份模板。");
      return;
    }
    if (!selectedTemplateBed?.current_patient_id) {
      setError("请先选择需要归档的床位或患者。");
      return;
    }
    try {
      setBusy(true);
      setError("");
      const draft = await api.createDocumentDraft(selectedTemplateBed.current_patient_id, templateLaunchNotes.trim(), {
        documentType: selectedTemplateDocumentType,
        templateId: selectedTemplate.id,
        templateText: selectedTemplate.template_text,
        templateName: selectedTemplate.name,
        requestedBy: user?.id,
        bedNo: selectedTemplateBed.bed_no,
        patientName: selectedTemplateBed.patient_name,
      });
      const seededDraft = selectedTemplateForm
        ? hydrateDraftForEditing(draft, {
            standardForm: selectedTemplateForm,
            context: {
              patient_id: draft.patient_id,
              patient_name: selectedTemplateBed.patient_name,
              full_name: selectedTemplateBed.patient_name,
              bed_no: selectedTemplateBed.bed_no,
              risk_level: selectedTemplateBed.risk_level,
              risk_tags: Array.isArray(selectedTemplateBed.risk_tags) ? selectedTemplateBed.risk_tags : [],
              pending_tasks: Array.isArray(selectedTemplateBed.pending_tasks) ? selectedTemplateBed.pending_tasks : [],
              latest_observations: [],
            } as any,
          })
        : draft;
      setDrafts((current) => [seededDraft, ...current.filter((item) => item.id !== seededDraft.id)]);
      setPatientArchiveMeta((current) => ({
        ...current,
        [seededDraft.patient_id]: {
          bedNo: selectedTemplateBed.bed_no,
          patientName: selectedTemplateBed.patient_name,
        },
      }));
      setTemplateLaunchNotes("");
      setView("inbox");
      setInboxTab("drafts");
      openEditor(seededDraft);
      void load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "模板草稿创建失败，请检查模板服务与患者上下文接口。"));
    } finally {
      setBusy(false);
    }
  };

  const saveDraftEdit = async () => {
    if (!editingDraft) {
      return;
    }
    try {
      setBusy(true);
      await api.editDraft(editingDraft.id, {
        draftText,
        editedBy: user?.id,
        structuredFields: editingStructuredFields,
      });
      resetEditor();
      await load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "草稿保存失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const reviewDraft = async (draftId: string) => {
    try {
      setBusy(true);
      await api.reviewDraft(draftId, user?.id || "u_nurse_01");
      await load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "草稿审核失败。"));
    } finally {
      setBusy(false);
    }
  };

  const submitDraft = async (draftId: string) => {
    try {
      setBusy(true);
      await api.submitDraft(draftId, user?.id || "u_nurse_01");
      if (editingDraft?.id === draftId) {
        resetEditor();
      }
      await load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "草稿归档失败。"));
    } finally {
      setBusy(false);
    }
  };

  const approveTask = async (taskId: string, approvalIds?: string[]) => {
    try {
      setBusy(true);
      await api.approveAgentQueueTask({ taskId, approvalIds, decidedBy: user?.id });
      await load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "任务批准失败。"));
    } finally {
      setBusy(false);
    }
  };

  const rejectTask = async (taskId: string, approvalIds?: string[]) => {
    try {
      setBusy(true);
      await api.rejectAgentQueueTask({ taskId, approvalIds, decidedBy: user?.id, comment: "先退回人工确认" });
      await load({ silent: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "任务退回失败。"));
    } finally {
      setBusy(false);
    }
  };

  const addFriend = async () => {
    const account = friendAccount.trim();
    if (!account || !user?.id) {
      return;
    }
    try {
      setBusy(true);
      await api.addCollabContact(user.id, account);
      setFriendAccount("");
      await load({ silent: true });
      setView("contacts");
    } catch (err) {
      setError(getApiErrorMessage(err, "添加好友失败，请确认账号。"));
    } finally {
      setBusy(false);
    }
  };

  const openContactChat = async (contact: CollabAccount) => {
    if (!user?.id) {
      return;
    }
    try {
      setBusy(true);
      const session = await api.openDirectSession({ userId: user.id, contactUserId: contact.id });
      navigation.navigate("MessageThread", {
        kind: "direct",
        sessionId: session.id,
        title: normalizePersonName(contact.full_name, contact.account),
        contactUserId: contact.id,
      });
    } catch (err) {
      setError(getApiErrorMessage(err, "打开会话失败。"));
    } finally {
      setBusy(false);
    }
  };

  const sessionRows = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return directSessions.filter((item) => {
      const text = `${normalizePersonName(item.contact?.full_name, item.contact?.account || "")} ${item.contact?.title || ""} ${sessionPreview(item)}`.toLowerCase();
      return !keyword || text.includes(keyword);
    });
  }, [directSessions, searchText]);

  const contactRows = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return contacts.filter((item) => {
      const text = `${normalizePersonName(item.full_name, item.account)} ${item.title || ""} ${item.account}`.toLowerCase();
      return !keyword || text.includes(keyword);
    });
  }, [contacts, searchText]);

  const inboxKeyword = searchText.trim().toLowerCase();

  const filteredQueueTasks = useMemo(() => {
    if (!inboxKeyword) {
      return queueTasks;
    }
    return queueTasks.filter((item) => {
      const haystack = `${item.payload.mission_title || ""} ${getWorkflowTypeLabel(item.workflow_type)} ${item.payload.user_input || ""} ${item.summary || ""} ${item.payload.bed_no || ""}`.toLowerCase();
      return haystack.includes(inboxKeyword);
    });
  }, [queueTasks, inboxKeyword]);

  const filteredRuns = useMemo(() => {
    if (!inboxKeyword) {
      return runs;
    }
    return runs.filter((item) => {
      const haystack = `${getWorkflowTypeLabel(item.workflow_type)} ${item.summary || ""} ${item.bed_no || ""} ${item.patient_name || ""} ${getStatusLabel(item.status)}`.toLowerCase();
      return haystack.includes(inboxKeyword);
    });
  }, [runs, inboxKeyword]);

  const activeDrafts = useMemo(
    () =>
      mergeDraftRows([...drafts])
        .filter((item) => item.status !== "submitted")
        .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || ""))),
    [drafts]
  );
  const wardBedByNo = useMemo(() => {
    const map = new Map<string, BedOverview>();
    wardBeds.forEach((item) => {
      const bedNo = normalizeBedNo(item.bed_no);
      if (bedNo) {
        map.set(bedNo, item);
      }
    });
    return map;
  }, [wardBeds]);
  const wardBedByPatientId = useMemo(() => {
    const map = new Map<string, BedOverview>();
    wardBeds.forEach((item) => {
      const patientId = String(item.current_patient_id || "").trim();
      if (patientId) {
        map.set(patientId, item);
      }
    });
    return map;
  }, [wardBeds]);
  const wardBedsByPatientName = useMemo(() => {
    const map = new Map<string, BedOverview[]>();
    wardBeds.forEach((item) => {
      const patientName = normalizePersonName(item.patient_name, "");
      if (!patientName) {
        return;
      }
      map.set(patientName, [...(map.get(patientName) || []), item]);
    });
    return map;
  }, [wardBeds]);

  const filteredDrafts = useMemo(() => {
    const cleanedDrafts = activeDrafts.filter((item) => {
      const meta = patientArchiveMeta[item.patient_id] || {};
      return !isDraftObsoleteForActiveBed(item, meta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName);
    });
    if (!inboxKeyword) {
      return cleanedDrafts;
    }
    return cleanedDrafts.filter((item) => {
      const meta = patientArchiveMeta[item.patient_id] || {};
      const identity = getDraftArchiveIdentity(item, meta);
      const haystack =
        `${getDocumentTypeLabel(item.document_type)} ${item.draft_text} ${getStatusLabel(item.status)} ${item.patient_id} ${identity.title} ${identity.subtitle} ${meta.bedNo || ""} ${meta.patientName || ""}`.toLowerCase();
      return haystack.includes(inboxKeyword);
    });
  }, [activeDrafts, inboxKeyword, patientArchiveMeta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName]);

  const draftsByBed = useMemo(() => {
    const groups = new Map<
      string,
      {
        key: string;
        title: string;
        subtitle: string;
        bedNo?: string;
        patientId?: string;
        identityScore: number;
        itemMap: Map<string, DocumentDraft>;
      }
    >();
    filteredDrafts.forEach((item) => {
      const meta = patientArchiveMeta[item.patient_id] || {};
      const identity = resolveCanonicalDraftIdentity(item, meta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName);
      const currentWardBed =
        (identity.bedNo ? wardBedByNo.get(normalizeBedNo(identity.bedNo)) : undefined) ||
        wardBedByPatientId.get(String(identity.patientId || item.patient_id || "").trim());
      const preferredPatientId = currentWardBed?.current_patient_id || identity.patientId || item.patient_id;
      const archiveKey = identity.bedNo ? `bed:${identity.bedNo}` : preferredPatientId ? `patient:${preferredPatientId}` : identity.groupKey;
      const archiveTitle = identity.title;
      const archiveSubtitle = identity.subtitle;
      const identityScore = getArchiveIdentityScore(identity);
      const dedupeKey = `${item.document_type}:${item.status}`;
      const hit = groups.get(archiveKey);
      if (hit) {
        const current = hit.itemMap.get(dedupeKey);
        const currentMatchesPreferred = current?.patient_id === preferredPatientId;
        const nextMatchesPreferred = item.patient_id === preferredPatientId;
        if (
          !current ||
          (nextMatchesPreferred && !currentMatchesPreferred) ||
          (nextMatchesPreferred === currentMatchesPreferred && getDraftScore(item) >= getDraftScore(current))
        ) {
          hit.itemMap.set(dedupeKey, item);
        }
        if (identityScore >= hit.identityScore) {
          hit.title = archiveTitle;
          hit.subtitle = archiveSubtitle;
          hit.bedNo = identity.bedNo;
          hit.patientId = preferredPatientId;
          hit.identityScore = identityScore;
        }
      } else {
        groups.set(archiveKey, {
          key: archiveKey,
          title: archiveTitle,
          subtitle: archiveSubtitle,
          bedNo: identity.bedNo,
          patientId: preferredPatientId,
          identityScore,
          itemMap: new Map([[dedupeKey, item]]),
        });
      }
    });
    const normalizedGroups = Array.from(groups.values()).map((group) => {
        const allItems = Array.from(group.itemMap.values()).sort((a, b) =>
          String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || ""))
        );
        const currentPatientId = group.bedNo
          ? wardBedByNo.get(normalizeBedNo(group.bedNo))?.current_patient_id || group.patientId
          : group.patientId;
        const currentPatientItems = currentPatientId ? allItems.filter((item) => item.patient_id === currentPatientId) : [];
        return {
          key: group.key,
          title: group.title,
          subtitle: group.subtitle,
          bedNo: group.bedNo,
          patientId: currentPatientId,
          identityScore: group.identityScore,
          items: currentPatientItems.length ? currentPatientItems : allItems,
        };
      });
    const dedupedGroups = new Map<string, (typeof normalizedGroups)[number]>();
    normalizedGroups.forEach((group) => {
      const mergedKey = getDraftGroupMergeKey(group);
      const current = dedupedGroups.get(mergedKey);
      if (!current) {
        dedupedGroups.set(mergedKey, group);
        return;
      }
      const mergedItems = mergeDraftRows([...current.items, ...group.items]).sort((a, b) =>
        String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || ""))
      );
      dedupedGroups.set(mergedKey, {
        ...current,
        title: current.identityScore >= group.identityScore ? current.title : group.title,
        subtitle: current.identityScore >= group.identityScore ? current.subtitle : group.subtitle,
        bedNo: current.bedNo || group.bedNo,
        patientId: current.patientId || group.patientId,
        identityScore: Math.max(current.identityScore, group.identityScore),
        items: mergedItems,
      });
    });
    return Array.from(dedupedGroups.values()).sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  }, [filteredDrafts, patientArchiveMeta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName]);

  const resolvedDraftGroups = useMemo(() => draftsByBed.filter((group) => group.patientId || group.bedNo), [draftsByBed]);
  const unresolvedDraftGroups = useMemo(() => draftsByBed.filter((group) => !group.patientId && !group.bedNo), [draftsByBed]);
  const visibleDrafts = useMemo(() => draftsByBed.flatMap((group) => group.items), [draftsByBed]);

  const openDraftGroupArchive = (group: (typeof draftsByBed)[number]) => {
    const anchorDraft = group.items[0];
    const anchorMeta = anchorDraft ? patientArchiveMeta[anchorDraft.patient_id] || {} : {};
    const target = anchorDraft
      ? resolveDraftNavigationTarget(anchorDraft, anchorMeta, wardBedByNo, wardBedByPatientId, wardBedsByPatientName)
      : {
          patientId: group.patientId,
          bedNo: group.bedNo,
        };
    const patientId = target.patientId || group.patientId || anchorDraft?.patient_id || "";
    const bedNo = target.bedNo || group.bedNo || normalizeBedNo(group.title);
    if (!patientId && !bedNo) {
      return;
    }
    navigation.navigate("PatientDetail", { patientId, bedNo });
  };

  const filteredTemplates = useMemo(() => {
    const rows = [...templates].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    if (!inboxKeyword) {
      return rows;
    }
    return rows.filter((item) => {
      const haystack = `${item.name} ${getDocumentTypeLabel(item.document_type)} ${(item.trigger_keywords || []).join(" ")} ${item.template_text}`.toLowerCase();
      return haystack.includes(inboxKeyword);
    });
  }, [templates, inboxKeyword]);

  useEffect(() => {
    if (!filteredTemplates.length) {
      if (selectedTemplateId) {
        setSelectedTemplateId("");
      }
      return;
    }
    if (!filteredTemplates.some((item) => item.id === selectedTemplateId)) {
      setSelectedTemplateId(filteredTemplates[0].id);
    }
  }, [filteredTemplates, selectedTemplateId]);

  const templateBedOptions = useMemo(
    () =>
      [...wardBeds]
        .filter((item) => item.current_patient_id)
        .sort((a, b) => String(a.bed_no || "").localeCompare(String(b.bed_no || ""), "zh-CN", { numeric: true })),
    [wardBeds]
  );

  useEffect(() => {
    if (!templateBedOptions.length) {
      if (selectedTemplatePatientId) {
        setSelectedTemplatePatientId("");
      }
      return;
    }
    if (!templateBedOptions.some((item) => item.current_patient_id === selectedTemplatePatientId)) {
      setSelectedTemplatePatientId(templateBedOptions[0].current_patient_id || "");
    }
  }, [selectedTemplatePatientId, templateBedOptions]);

  const selectedTemplate = useMemo(
    () => filteredTemplates.find((item) => item.id === selectedTemplateId) || filteredTemplates[0] || null,
    [filteredTemplates, selectedTemplateId]
  );

  const selectedTemplateDocumentType = selectedTemplate?.document_type || "nursing_note";
  const selectedTemplateForm = useMemo(
    () => standardForms.find((item) => item.document_type === selectedTemplateDocumentType) || null,
    [selectedTemplateDocumentType, standardForms]
  );
  const selectedTemplateBed = useMemo(
    () =>
      templateBedOptions.find((item) => item.current_patient_id === selectedTemplatePatientId) || templateBedOptions[0] || null,
    [selectedTemplatePatientId, templateBedOptions]
  );
  const selectedTemplateSourceRefs = useMemo(
    () =>
      (selectedTemplate?.source_refs?.length ? selectedTemplate.source_refs : selectedTemplateForm?.source_refs) || [],
    [selectedTemplate, selectedTemplateForm]
  );
  const selectedTemplateSections = selectedTemplateForm?.sections || [];
  const selectedTemplateColumns = selectedTemplateForm?.sheet_columns || [];
  const templateWorkbenchWide = screenWidth >= 1680;
  const templateInnerScrollEnabled = true;
  const latestAiMessage = useMemo(
    () => aiSession?.messages?.slice().reverse().find((item) => item.role === "assistant") || null,
    [aiSession?.messages]
  );

  const aiSubtitle =
    buildAssistantPreviewText(latestAiMessage, 4) ||
    (aiSession?.memorySummary ? formatAiText(aiSession.memorySummary) : "") ||
    "支持通用护理问答、病区调度、文书草稿和交接班协同。";

  const pendingCount = queueTasks.filter((item) => item.status === "waiting_approval").length;
  const draftCount = visibleDrafts.length;
  const draftFieldStats = useMemo(() => {
    const totalMissing = visibleDrafts.reduce((sum, item) => sum + Number(item.structured_fields?.field_summary?.missing || 0), 0);
    const readyForReview = visibleDrafts.filter((item) => item.status === "draft" && Number(item.structured_fields?.field_summary?.missing || 0) === 0).length;
    const readyForArchive = visibleDrafts.filter((item) => item.status === "reviewed").length;
    return {
      totalMissing,
      readyForReview,
      readyForArchive,
    };
  }, [visibleDrafts]);

  const riskRows = useMemo(
    () =>
      [...wardBeds]
        .filter((item) => item.current_patient_id)
        .map((item) => ({
          ...item,
          badge: buildClinicalRiskBadge(item),
        })),
    [wardBeds]
  );

  const riskBoard = useMemo(
    () => [...riskRows].filter((item) => item.badge.canUseHeatmap).sort((a, b) => b.badge.sortKey - a.badge.sortKey).slice(0, 4),
    [riskRows]
  );

  const riskPendingReview = useMemo(
    () => riskRows.filter((item) => !item.badge.canUseHeatmap),
    [riskRows]
  );

  const todayTodos = useMemo(() => {
    const items: Array<{ title: string; subtitle: string; tone: "warning" | "info" | "success" }> = [];
    if (pendingCount) {
      items.push({
        title: `${pendingCount} 个协作任务待批准`,
        subtitle: "优先处理需要人工确认的闭环动作，避免任务卡在审核节点。",
        tone: "warning",
      });
    }
    if (draftCount) {
      items.push({
        title: `${draftCount} 份文书草稿待整理`,
        subtitle: "审核后自动归档到患者病例，收件箱只保留未归档草稿。",
        tone: "info",
      });
    }
    if (draftsByBed.length) {
      items.push({
        title: `优先整理 ${draftsByBed[0].title}`,
        subtitle: "建议先进入患者档案补齐字段，再完成审核与归档。",
        tone: "info",
      });
    }
    if (riskBoard.length) {
      items.push({
        title: `${formatBedLabel(riskBoard[0].bed_no)} 需重点盯防`,
        subtitle: `${normalizePersonName(riskBoard[0].patient_name, "重点患者")} · ${riskBoard[0].badge.shortReason}`,
        tone: riskBoard[0].badge.tone === "danger" ? "warning" : "info",
      });
    } else if (riskPendingReview.length) {
      items.push({
        title: `${riskPendingReview.length} 床待人工核对`,
        subtitle: "病区有风险线索，但未收到可靠分层结果，今日待办只提示核对，不生成着色热力图。",
        tone: "warning",
      });
    }
    const latestHandover = runs.find((item) => item.workflow_type === "handover_generate");
    if (latestHandover?.summary) {
      items.push({
        title: "今日交接摘要",
        subtitle: latestHandover.summary,
        tone: "success",
      });
    }
    const latestAutonomous = runs.find((item) => item.workflow_type === "autonomous_care");
    if (latestAutonomous?.summary) {
      items.push({
        title: "智能协作重点",
        subtitle: latestAutonomous.summary,
        tone: "info",
      });
    }
    if (!items.length) {
      items.push({
        title: "今日待办已清空",
        subtitle: "当前没有待批准任务，新的交接和文书指令会自动汇入这里。",
        tone: "success",
      });
    }
    return items.slice(0, 4);
  }, [draftCount, draftsByBed, pendingCount, riskBoard, riskPendingReview.length, runs]);

  const timelineEvents = useMemo(() => {
    const items: Array<{ time: string; title: string; subtitle: string; tone: "warning" | "info" | "success" }> = [];

    riskBoard.forEach((bed, index) => {
      items.push({
        time: `优先 ${index + 1}`,
        title: `${formatBedLabel(bed.bed_no)}${bed.patient_name ? ` ${normalizePersonName(bed.patient_name)}` : ""}`.trim(),
        subtitle: `${bed.badge.label} · ${bed.pending_tasks.slice(0, 2).join("、") || bed.badge.shortReason}`,
        tone: bed.badge.label === "危急" || bed.badge.label === "高危" ? "warning" : "info",
      });
    });

    if (!riskBoard.length && riskPendingReview.length) {
      items.push({
        time: "待核对",
        title: `${riskPendingReview.length} 床未进入热力图`,
        subtitle: "缺少结构化风险分层字段，已阻止系统自动排序，请先人工确认风险等级。",
        tone: "warning",
      });
    }

    queueTasks
      .filter((item) => item.status === "waiting_approval")
      .slice(0, 2)
      .forEach((item) => {
        items.push({
          time: formatConversationTime(item.updated_at),
          title: item.payload.mission_title || "协作任务待批准",
          subtitle: item.summary || item.payload.user_input || "等待人工确认后继续执行",
          tone: "warning",
        });
      });

    visibleDrafts.slice(0, 2).forEach((draft) => {
      const meta = patientArchiveMeta[draft.patient_id] || {};
      const identity = getDraftArchiveIdentity(draft, meta);
      items.push({
        time: formatConversationTime(draft.updated_at),
        title: `${identity.title} · ${getDocumentTypeLabel(draft.document_type)}`,
        subtitle: formatArchiveHint(draft),
        tone: "info",
      });
    });

    if (!items.length) {
      items.push({
        time: "今日",
        title: "待办已清空",
        subtitle: "新的协作、交接班和文书草稿会自动汇入这里。",
        tone: "success",
      });
    }

    return items.slice(0, 6);
  }, [patientArchiveMeta, queueTasks, riskBoard, riskPendingReview.length, visibleDrafts]);

  const handoverBoard = useMemo(() => {
    const latestHandover = runs.find((item) => item.workflow_type === "handover_generate" && item.summary);
    const latestAutonomous = runs.find((item) => item.workflow_type === "autonomous_care" && item.summary);
    const cards: Array<{ title: string; summary: string; tone: "warning" | "info" | "success" }> = [];

    if (riskBoard.length) {
      cards.push({
        title: "本班高风险交接重点",
        summary: riskBoard
          .map((bed) => `${formatBedLabel(bed.bed_no)}${bed.patient_name ? ` ${normalizePersonName(bed.patient_name)}` : ""}：${bed.badge.shortReason}`)
          .join("；"),
        tone: "warning",
      });
    } else if (riskPendingReview.length) {
      cards.push({
        title: "高风险交接待护士确认",
        summary: "当前只收到风险线索，未收到结构化风险分层，因此不自动拼接高风险交接摘要，避免交班口径失真。",
        tone: "warning",
      });
    }
    if (latestHandover?.summary) {
      cards.push({
        title: "最新交接班摘要",
        summary: latestHandover.summary,
        tone: "success",
      });
    }
    if (latestAutonomous?.summary) {
      cards.push({
        title: "智能协作建议",
        summary: latestAutonomous.summary,
        tone: "info",
      });
    }
    if (!cards.length) {
      cards.push({
        title: "交接班摘要待生成",
        summary: "可以直接下达“生成今天病区交接班摘要”或“整理今日待办与交接重点”。",
        tone: "info",
      });
    }
    return cards.slice(0, 3);
  }, [riskBoard, riskPendingReview.length, runs]);

  const visualPanels = [
    { id: "risk", label: "风险热力图" },
    { id: "timeline", label: "今日时间轴" },
    { id: "handover", label: "交接摘要" },
  ] as const;
  const resolvedVisualCardWidth = visualCardWidth || Math.max(screenWidth - 32, 280);

  const scrollToVisualCard = (index: number) => {
    setActiveVisualCard(index);
    visualCarouselRef.current?.scrollTo({
      x: resolvedVisualCardWidth * index,
      animated: true,
    });
  };

  return (
    <ScreenShell
      title="协作"
      subtitle={compactLayout ? undefined : "像工作收件箱一样管理消息、通讯录和文书草稿"}
      rightNode={<ActionButton label="刷新" onPress={() => void load()} variant="secondary" style={styles.refreshButton} />}
    >
      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.segmentRow}>
        {[
          { id: "messages", label: "消息" },
          { id: "contacts", label: "通讯录" },
          { id: "inbox", label: "收件箱" },
        ].map((item) => {
          const active = view === item.id;
          return (
            <Pressable key={item.id} style={[styles.segmentChip, active && styles.segmentChipActive]} onPress={() => setView(item.id as HubView)}>
              <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{item.label}</Text>
            </Pressable>
          );
        })}
      </View>

      <TextInput
        value={searchText}
        onChangeText={setSearchText}
        placeholder={view === "contacts" ? "搜索联系人或账号" : "搜索消息、联系人或文书"}
        placeholderTextColor={colors.subText}
        style={styles.searchInput}
      />

      {view === "messages" ? (
        <>
          <PanelSection title="今日待办">
            {todayTodos.map((item, index) => (
              <View key={`${item.title}-${index}`} style={styles.todoRow}>
                <StatusPill text={item.tone === "warning" ? "优先" : item.tone === "success" ? "已整理" : "跟进"} tone={item.tone} />
                <View style={styles.todoBody}>
                  <Text style={styles.todoTitle}>{item.title}</Text>
                  <Text style={styles.todoSubtitle}>{item.subtitle}</Text>
                </View>
              </View>
            ))}
          </PanelSection>

          <PanelSection title="会话">
            <View style={styles.panelSection}>
              <View style={styles.visualHeader}>
                <View style={styles.visualHeaderText}>
                  <Text style={styles.panelSectionTitle}>可视化看板</Text>
                  <Text style={styles.visualHint}>左滑即可查看风险热力图、今日待办时间轴和交接班摘要，避免三个模块在小屏上相互遮挡。</Text>
                </View>
                <StatusPill text="左滑查看" tone="info" />
              </View>

              <View style={styles.visualChipRow}>
                {visualPanels.map((item, index) => {
                  const active = activeVisualCard === index;
                  return (
                    <Pressable key={item.id} style={[styles.visualChip, active && styles.visualChipActive]} onPress={() => scrollToVisualCard(index)}>
                      <Text style={[styles.visualChipText, active && styles.visualChipTextActive]}>{item.label}</Text>
                    </Pressable>
                  );
                })}
              </View>

              <View
                style={styles.visualDeck}
                onLayout={(event) => {
                  const nextWidth = Math.max(event.nativeEvent.layout.width, 280);
                  if (Math.abs(nextWidth - visualCardWidth) > 4) {
                    setVisualCardWidth(nextWidth);
                  }
                }}
              >
                <ScrollView
                  ref={visualCarouselRef}
                  horizontal
                  pagingEnabled
                  nestedScrollEnabled
                  directionalLockEnabled
                  showsHorizontalScrollIndicator={false}
                  onMomentumScrollEnd={(event) => {
                    const nextIndex = Math.round(event.nativeEvent.contentOffset.x / resolvedVisualCardWidth);
                    setActiveVisualCard(Math.max(0, Math.min(visualPanels.length - 1, nextIndex)));
                  }}
                >
                  <View style={[styles.visualPage, { width: resolvedVisualCardWidth }]}>
                    <View style={styles.flatPanel}>
                      <View style={styles.panelSectionHead}>
                        <Text style={styles.panelSectionTitle}>病区风险热力图</Text>
                      </View>
                      <View style={styles.riskHeatGrid}>
                        {riskBoard.map((bed) => (
                          <Pressable
                            key={`risk-${bed.id}`}
                            style={[
                              styles.riskHeatCell,
                              bed.badge.tone === "danger"
                                ? styles.riskToneDanger
                                : bed.badge.tone === "warning"
                                  ? styles.riskToneWarning
                                  : styles.riskToneInfo,
                            ]}
                            onPress={() => {
                              if (bed.current_patient_id) {
                                navigation.navigate("PatientDetail", { patientId: bed.current_patient_id, bedNo: bed.bed_no });
                              }
                            }}
                          >
                            <View style={styles.riskHeatHead}>
                              <View
                                style={[
                                  styles.bedColorPlate,
                                  {
                                    backgroundColor: buildNursingLevelTone(bed).backgroundColor,
                                    borderColor: buildNursingLevelTone(bed).borderColor,
                                  },
                                ]}
                              >
                                <Text
                                  style={[
                                    styles.bedColorPlateText,
                                    { color: buildNursingLevelTone(bed).textColor },
                                  ]}
                                >
                                  {formatBedLabel(bed.bed_no)}
                                </Text>
                              </View>
                            </View>
                            <Text style={styles.riskHeatName} numberOfLines={1}>
                              {normalizePersonName(bed.patient_name, "待确认")}
                            </Text>
                            <Text style={styles.riskHeatMeta} numberOfLines={2}>
                              {bed.badge.label} · {bed.badge.shortReason}
                            </Text>
                          </Pressable>
                        ))}
                      </View>
                      {!riskBoard.length ? (
                        <Text style={styles.emptyHint}>
                          {riskPendingReview.length
                            ? "当前只有待核对床位，系统已停止自动着色，请先完成风险分层。"
                            : "当前还没有可展示的重点床位。"}
                        </Text>
                      ) : null}
                    </View>
                  </View>

                  <View style={[styles.visualPage, { width: resolvedVisualCardWidth }]}>
                    <View style={styles.flatPanel}>
                      <View style={styles.panelSectionHead}>
                        <Text style={styles.panelSectionTitle}>今日待办时间轴</Text>
                      </View>
                      {timelineEvents.map((item, index) => (
                        <View key={`${item.title}-${index}-timeline`} style={styles.timelineRow}>
                          <View style={styles.timelineTimeWrap}>
                            <Text style={styles.timelineTime}>{item.time}</Text>
                            <View
                              style={[
                                styles.timelineDot,
                                item.tone === "warning"
                                  ? styles.timelineDotWarning
                                  : item.tone === "success"
                                    ? styles.timelineDotSuccess
                                    : styles.timelineDotInfo,
                              ]}
                            />
                          </View>
                          <View style={styles.timelineBody}>
                            <Text style={styles.timelineTitle}>{item.title}</Text>
                            <Text style={styles.timelineSubtitle}>{item.subtitle}</Text>
                          </View>
                        </View>
                      ))}
                    </View>
                  </View>

                  <View style={[styles.visualPage, { width: resolvedVisualCardWidth }]}>
                    <View style={styles.flatPanel}>
                      <View style={styles.panelSectionHead}>
                        <Text style={styles.panelSectionTitle}>交接班摘要看板</Text>
                      </View>
                      {handoverBoard.map((item, index) => (
                        <View key={`${item.title}-${index}-handover`} style={styles.boardRow}>
                          <StatusPill text={item.tone === "warning" ? "重点交班" : item.tone === "success" ? "已生成" : "待补充"} tone={item.tone} />
                          <View style={styles.boardBody}>
                            <Text style={styles.boardTitle}>{item.title}</Text>
                            <Text style={styles.boardSummary}>{item.summary}</Text>
                          </View>
                        </View>
                      ))}
                    </View>
                  </View>
                </ScrollView>
              </View>

              <View style={styles.visualDotRow}>
                {visualPanels.map((item, index) => (
                  <View key={`${item.id}-dot`} style={[styles.visualDot, activeVisualCard === index && styles.visualDotActive]} />
                ))}
              </View>
            </View>

            <ListRow
              title={AI_AGENT_CHAT_TITLE}
              subtitle={aiSubtitle}
              subtitleLines={4}
              trailing={formatConversationTime(aiSession?.updatedAt)}
              leading="智"
              badgeText="临床协作"
              onPress={() =>
                navigation.navigate("MessageThread", {
                  kind: "ai",
                  title: AI_AGENT_CHAT_TITLE,
                  sessionId: aiSession?.conversationId || AI_AGENT_CHAT_SESSION_ID,
                })
              }
            />

            {sessionRows.map((session) => (
              <ListRow
                key={session.id}
                title={normalizePersonName(session.contact?.full_name, session.contact?.account || "好友会话")}
                subtitle={sessionPreview(session)}
                subtitleLines={3}
                trailing={formatConversationTime(session.updated_at)}
                unreadCount={session.unread_count}
                leading={normalizePersonName(session.contact?.full_name, "协作").slice(0, 1)}
                badgeText={session.contact?.title || undefined}
                onPress={() =>
                  navigation.navigate("MessageThread", {
                    kind: "direct",
                    sessionId: session.id,
                    title: normalizePersonName(session.contact?.full_name, session.contact?.account || "好友会话"),
                    contactUserId: session.contact_user_id,
                  })
                }
              />
            ))}

            {!loading && !sessionRows.length ? (
              <View style={styles.emptyActionStack}>
                <Text style={styles.emptyHint}>还没有好友消息，可从通讯录发起会话。</Text>
                <ActionButton label="去通讯录发起会话" onPress={() => setView("contacts")} variant="secondary" style={styles.smallAction} />
              </View>
            ) : null}
          </PanelSection>
        </>
      ) : null}

      {view === "contacts" ? (
        <>
          <SurfaceCard>
            <Text style={styles.panelSectionTitle}>添加好友</Text>
            <Text style={styles.metaText}>输入系统账号，例如 doctor01、charge01，把医生或护士长加入通讯录。</Text>
            <View style={styles.friendRow}>
              <TextInput
                value={friendAccount}
                onChangeText={setFriendAccount}
                placeholder="输入好友账号"
                placeholderTextColor={colors.subText}
                style={styles.friendInput}
              />
              <ActionButton label="添加" onPress={addFriend} disabled={busy} style={styles.friendButton} />
            </View>
          </SurfaceCard>

          <PanelSection title="联系人">
            {contactRows.map((contact) => (
              <ListRow
                key={contact.id}
                title={normalizePersonName(contact.full_name, contact.account)}
                subtitle={`${contact.title || getRoleLabel(contact.role_code)} · ${contact.account}`}
                leading={normalizePersonName(contact.full_name, contact.account).slice(0, 1)}
                badgeText={contact.department || undefined}
                onPress={() => openContactChat(contact)}
              />
            ))}
            {!loading && !contactRows.length ? <Text style={styles.emptyHint}>通讯录暂时为空，先添加一个好友。</Text> : null}
          </PanelSection>
        </>
      ) : null}

      {view === "inbox" ? (
        <>
          <PanelSection title="今日待办">
            {todayTodos.map((item, index) => (
              <View key={`${item.title}-${index}-inbox`} style={styles.todoRow}>
                <StatusPill text={item.tone === "warning" ? "优先" : item.tone === "success" ? "已整理" : "跟进"} tone={item.tone} />
                <View style={styles.todoBody}>
                  <Text style={styles.todoTitle}>{item.title}</Text>
                  <Text style={styles.todoSubtitle}>{item.subtitle}</Text>
                </View>
              </View>
            ))}
          </PanelSection>

          <View style={styles.metricRow}>
            <View style={styles.metricBox}>
              <Text style={styles.metricLabel}>待批准</Text>
              <Text style={styles.metricValue}>{pendingCount}</Text>
            </View>
            <View style={styles.metricBox}>
              <Text style={styles.metricLabel}>草稿</Text>
              <Text style={styles.metricValue}>{draftCount}</Text>
            </View>
            <View style={styles.metricBox}>
              <Text style={styles.metricLabel}>模板</Text>
              <Text style={styles.metricValue}>{templates.length}</Text>
            </View>
          </View>

          <View style={styles.segmentRow}>
            {[
              { id: "approvals", label: "待处理" },
              { id: "drafts", label: "文书草稿" },
              { id: "templates", label: "模板库" },
            ].map((item) => {
              const active = inboxTab === item.id;
              return (
                <Pressable key={item.id} style={[styles.segmentChip, active && styles.segmentChipActive]} onPress={() => setInboxTab(item.id as InboxTab)}>
                  <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{item.label}</Text>
                </Pressable>
              );
            })}
          </View>

          <ScrollView
            contentContainerStyle={styles.inboxContent}
            keyboardShouldPersistTaps="handled"
            nestedScrollEnabled
            showsVerticalScrollIndicator={false}
          >
            {inboxTab === "approvals" ? (
              <>
                <PanelSection title="协作待批准">
                  {filteredQueueTasks.map((task) => (
                    <View key={task.id} style={styles.inboxRow}>
                      <View style={styles.inboxRowHead}>
                        <Text style={styles.inboxTitle}>{task.payload.mission_title || getWorkflowTypeLabel(task.workflow_type)}</Text>
                        <StatusPill text={getStatusLabel(task.status)} tone={tone(task.status)} />
                      </View>
                      <Text style={styles.inboxText}>{task.payload.user_input || "未提供任务说明"}</Text>
                      <Text style={styles.metaText}>{task.summary || "等待执行或等待人工确认"}</Text>
                      {task.status === "waiting_approval" ? (
                        <View style={styles.actionRow}>
                          <ActionButton
                            label="批准"
                            onPress={() =>
                              approveTask(
                                task.id,
                                task.approvals.filter((item) => item.status === "pending").map((item) => item.id)
                              )
                            }
                            disabled={busy}
                            style={styles.smallAction}
                          />
                          <ActionButton
                            label="退回"
                            onPress={() =>
                              rejectTask(
                                task.id,
                                task.approvals.filter((item) => item.status === "pending").map((item) => item.id)
                              )
                            }
                            disabled={busy}
                            variant="secondary"
                            style={styles.smallAction}
                          />
                        </View>
                      ) : null}
                    </View>
                  ))}
                  {!loading && !filteredQueueTasks.length ? <Text style={styles.emptyHint}>当前没有命中的协作任务。</Text> : null}
                </PanelSection>

                <PanelSection title="最近运行">
                  {filteredRuns.slice(0, 8).map((run) => (
                    <View key={run.id} style={styles.inboxRow}>
                      <View style={styles.inboxRowHead}>
                        <Text style={styles.inboxTitle}>
                          {getWorkflowTypeLabel(run.workflow_type)}
                          {run.bed_no ? ` · ${formatBedLabel(run.bed_no)}` : ""}
                        </Text>
                        <StatusPill text={getStatusLabel(run.status)} tone={tone(run.status)} />
                      </View>
                      <Text style={styles.inboxText}>{run.summary || "暂无摘要"}</Text>
                      <Text style={styles.metaText}>{new Date(run.updated_at).toLocaleString()}</Text>
                    </View>
                  ))}
                </PanelSection>
              </>
            ) : null}

            {Boolean(inboxTab === "templates") ? (
              <PanelSection title="模板工作台">
                <Text style={styles.metaText}>
                  现在可以直接查看模板结构、选择床位并跳到专业编辑页；归档时会自动落到对应患者病例下。
                </Text>
                <View style={[styles.templateWorkbench, templateWorkbenchWide && styles.templateWorkbenchWide]}>
                  <View style={styles.templateCatalogColumn}>
                    {filteredTemplates.map((template) => {
                      const linkedForm =
                        standardForms.find((item) => item.document_type === (template.document_type || "nursing_note")) || null;
                      const active = template.id === selectedTemplate?.id;
                      return (
                        <Pressable
                          key={`picker-${template.id}`}
                          style={[styles.templateCard, active && styles.templateCardActive]}
                          onPress={() => setSelectedTemplateId(template.id)}
                        >
                          <View style={styles.templateCardHead}>
                            <Text style={styles.templateCardTitle}>{template.name}</Text>
                            <StatusPill
                              text={template.document_type ? getDocumentTypeLabel(template.document_type) : getSourceTypeLabel(template.source_type)}
                              tone={template.source_type === "system" ? "success" : "info"}
                            />
                          </View>
                          <Text style={styles.metaText}>
                            {(template.trigger_keywords || []).slice(0, 4).join(" / ") || "标准化临床模板"}
                          </Text>
                          <Text style={styles.metaText}>
                            {linkedForm
                              ? `结构化栏目 ${linkedForm.sections?.length || 0} 组 · 字段 ${linkedForm.field_count || 0} 项`
                              : "尚未匹配结构化字段，将按模板正文和通用字段协同编辑"}
                          </Text>
                          <Text style={styles.inboxText} numberOfLines={4}>
                            {formatAiText(template.template_text)}
                          </Text>
                        </Pressable>
                      );
                    })}
                    {!loading && !filteredTemplates.length ? <Text style={styles.emptyHint}>当前没有命中的模板。</Text> : null}
                  </View>

                  <View style={styles.templateDetailColumn}>
                    {selectedTemplate ? (
                      <>
                        <SurfaceCard style={styles.templateDetailCard}>
                          <View style={styles.templateCardHead}>
                            <Text style={styles.templateDetailTitle}>{selectedTemplate.name}</Text>
                            <StatusPill
                              text={selectedTemplate.source_type === "system" ? "标准模板" : "导入模板"}
                              tone={selectedTemplate.source_type === "system" ? "success" : "info"}
                            />
                          </View>
                          <Text style={styles.templateDetailMeta}>
                            文书类型：{getDocumentTypeLabel(selectedTemplateDocumentType)} · 更新时间：
                            {new Date(selectedTemplate.updated_at).toLocaleString()}
                          </Text>
                          <Text style={styles.templateDetailMeta}>
                            {selectedTemplateSourceRefs.length
                              ? `来源：${selectedTemplateSourceRefs.join(" / ")}`
                              : "来源：当前模板未附带来源说明"}
                          </Text>
                          {selectedTemplateForm ? (
                            <Text style={styles.templateDetailMeta}>
                              结构映射：{selectedTemplateForm.standard_family || "临床标准表单"} · 栏目 {selectedTemplateSections.length} 组 · 字段{" "}
                              {selectedTemplateForm.field_count} 项
                            </Text>
                          ) : (
                            <InfoBanner
                              title="结构化映射待补齐"
                              description="这份模板可以直接打开编辑，但暂时只能依赖模板正文和通用字段，建议后续再补一版标准字段映射。"
                              tone="warning"
                            />
                          )}
                        </SurfaceCard>

                        <View style={[styles.templateFlowGrid, templateWorkbenchWide && styles.templateFlowGridWide]}>
                          <SurfaceCard style={[styles.templateDetailCard, styles.templateFlowCard]}>
                            <Text style={styles.templateDetailLabel}>模板栏目</Text>
                            <ScrollView
                              style={[
                                styles.templateBedPicker,
                                templateWorkbenchWide && styles.templateBedPickerWide,
                                !templateInnerScrollEnabled && styles.templatePickerRelaxed,
                              ]}
                              contentContainerStyle={styles.templateChipGrid}
                              nestedScrollEnabled={templateInnerScrollEnabled}
                              scrollEnabled={templateInnerScrollEnabled}
                              showsVerticalScrollIndicator={templateInnerScrollEnabled}
                            >
                              {selectedTemplateSections.length ? (
                                selectedTemplateSections.map((section, index) => (
                                  <PickerChip
                                    key={`${section.key || section.title}-${index}`}
                                    active={false}
                                    label={section.title}
                                    note={section.key ? `映射键 ${section.key}` : "标准栏目"}
                                    onPress={() => undefined}
                                  />
                                ))
                              ) : (
                                <Text style={styles.metaText}>当前模板还没有返回结构化栏目，先展示正文模板与床位落点。</Text>
                              )}
                            </ScrollView>
                            <Text style={styles.templateDetailLabel}>表格字段</Text>
                            <ScrollView
                              style={[
                                styles.templateBedPicker,
                                templateWorkbenchWide && styles.templateBedPickerWide,
                                !templateInnerScrollEnabled && styles.templatePickerRelaxed,
                              ]}
                              contentContainerStyle={styles.templateChipGrid}
                              nestedScrollEnabled={templateInnerScrollEnabled}
                              scrollEnabled={templateInnerScrollEnabled}
                              showsVerticalScrollIndicator={templateInnerScrollEnabled}
                            >
                              {selectedTemplateColumns.length ? (
                                selectedTemplateColumns.map((column) => (
                                  <PickerChip
                                    key={column.key}
                                    active={false}
                                    label={column.label}
                                    note={column.required ? "必填" : column.section}
                                    onPress={() => undefined}
                                  />
                                ))
                              ) : (
                                <Text style={styles.metaText}>当前模板没有表格字段，编辑页会优先打开正文与结构化字段视图。</Text>
                              )}
                            </ScrollView>
                          </SurfaceCard>

                          <SurfaceCard style={[styles.templateDetailCard, styles.templatePreviewCard]}>
                            <Text style={styles.templateDetailLabel}>模板正文预览</Text>
                            <ScrollView
                              style={[styles.templateBodyPreview, !templateInnerScrollEnabled && styles.templateBodyPreviewRelaxed]}
                              contentContainerStyle={styles.templateBodyPreviewContent}
                              nestedScrollEnabled={templateInnerScrollEnabled}
                              scrollEnabled={templateInnerScrollEnabled}
                            >
                              <Text style={styles.templateBodyText}>{formatAiText(selectedTemplate.template_text)}</Text>
                            </ScrollView>
                          </SurfaceCard>
                        </View>

                        <View style={[styles.templateActionGrid, templateWorkbenchWide && styles.templateActionGridWide]}>
                          <SurfaceCard style={[styles.templateDetailCard, styles.templateActionCard]}>
                            <Text style={styles.templateDetailLabel}>归档床位</Text>
                            <ScrollView
                              style={[
                                styles.templateArchivePicker,
                                templateWorkbenchWide && styles.templateArchivePickerWide,
                                !templateInnerScrollEnabled && styles.templatePickerRelaxed,
                              ]}
                              contentContainerStyle={styles.templateChipGrid}
                              nestedScrollEnabled={templateInnerScrollEnabled}
                              scrollEnabled={templateInnerScrollEnabled}
                              showsVerticalScrollIndicator={templateInnerScrollEnabled}
                            >
                              {templateBedOptions.map((bed) => (
                                <PickerChip
                                  key={`bed-${bed.id}`}
                                  active={bed.current_patient_id === selectedTemplatePatientId}
                                  label={`${formatBedLabel(bed.bed_no)} ${normalizePersonName(bed.patient_name, "未命名患者")}`}
                                  note={buildClinicalRiskBadge(bed).shortReason}
                                  onPress={() => setSelectedTemplatePatientId(bed.current_patient_id || "")}
                                />
                              ))}
                            </ScrollView>
                            {selectedTemplateBed ? (
                              <Text style={styles.templateDetailMeta}>
                                当前归档落点：{formatBedLabel(selectedTemplateBed.bed_no)} ·
                                {normalizePersonName(selectedTemplateBed.patient_name, selectedTemplateBed.current_patient_id || "未命名患者")} ·
                                患者ID {selectedTemplateBed.current_patient_id}
                              </Text>
                            ) : (
                              <Text style={styles.metaText}>当前病区没有可用在床患者，暂时无法直接归档。</Text>
                            )}
                          </SurfaceCard>

                          <SurfaceCard style={[styles.templateDetailCard, styles.templateActionCard]}>
                            <Text style={styles.templateDetailLabel}>补充信息</Text>
                            <Text style={styles.templateDetailMeta}>先保存为文书草稿，再由护士审核，最后才提交归档到对应床号病例。</Text>
                            <TextInput
                              value={templateLaunchNotes}
                              onChangeText={setTemplateLaunchNotes}
                              placeholder="这里可直接录入护理要点、交接摘要或观察结果；也可以留空，先按标准模板开空白草稿再补。"
                              placeholderTextColor={colors.subText}
                              multiline
                              textAlignVertical="top"
                              style={styles.templateNotesInput}
                            />
                            <View style={styles.actionRow}>
                              <ActionButton
                                label={templateLaunchNotes.trim() ? "生成草稿并进入编辑页" : "按模板新建草稿"}
                                onPress={createDraftFromTemplate}
                                disabled={busy || !selectedTemplateBed?.current_patient_id}
                                style={styles.smallAction}
                              />
                              {selectedTemplateBed?.current_patient_id ? (
                                <ActionButton
                                  label="查看对应病例"
                                  onPress={() =>
                                    navigation.navigate("PatientDetail", {
                                      patientId: selectedTemplateBed.current_patient_id!,
                                      bedNo: selectedTemplateBed.bed_no,
                                    })
                                  }
                                  variant="secondary"
                                  disabled={busy}
                                  style={styles.smallAction}
                                />
                              ) : null}
                            </View>
                          </SurfaceCard>
                        </View>
                      </>
                    ) : (
                      <Text style={styles.emptyHint}>请先从模板库中选择一份标准模板。</Text>
                    )}
                  </View>
                </View>
              </PanelSection>
            ) : null}

            {inboxTab === "drafts" ? (
              <>
                {editingDraft ? (
                  <DocumentStructuredEditor
                    key={editingDraft.id}
                    draft={editingDraft}
                    draftText={draftText}
                    structuredFields={editingStructuredFields}
                    busy={busy}
                    onDraftTextChange={setDraftText}
                    onStructuredFieldsChange={setEditingStructuredFields}
                    onCancel={resetEditor}
                    onSave={saveDraftEdit}
                  />
                ) : null}

                <PanelSection title="护理文书草稿">
                  <Text style={styles.emptyHint}>
                    提交后的文书会自动归档到对应患者病例下，这里只保留待编辑、待审核、待提交的草稿，保持协作页整洁。
                  </Text>

                  <View style={styles.draftMetricRow}>
                    <View style={styles.draftMetricCard}>
                      <Text style={styles.draftMetricValue}>{draftFieldStats.totalMissing}</Text>
                      <Text style={styles.draftMetricLabel}>待补字段</Text>
                    </View>
                    <View style={styles.draftMetricCard}>
                      <Text style={styles.draftMetricValue}>{draftFieldStats.readyForReview}</Text>
                      <Text style={styles.draftMetricLabel}>可直接审核</Text>
                    </View>
                    <View style={styles.draftMetricCard}>
                      <Text style={styles.draftMetricValue}>{draftFieldStats.readyForArchive}</Text>
                      <Text style={styles.draftMetricLabel}>待归档</Text>
                    </View>
                  </View>

                  {resolvedDraftGroups.length ? (
                    <View style={styles.archiveEntryPanel}>
                      {resolvedDraftGroups.map((group) => (
                        <PatientArchiveRow
                          key={`${group.key}-entry`}
                          title={group.title}
                          subtitle={group.subtitle}
                          count={group.items.length}
                          onPress={() => openDraftGroupArchive(group)}
                        />
                      ))}
                    </View>
                  ) : null}

                  {resolvedDraftGroups.map((group) => (
                    <View key={group.key} style={styles.archiveGroup}>
                      <Pressable style={styles.archiveGroupHead} onPress={() => openDraftGroupArchive(group)}>
                        <Text style={styles.archiveGroupTitle}>{group.title}</Text>
                        {group.patientId || group.bedNo ? (
                          <ActionButton
                            label="查看档案"
                            onPress={() => openDraftGroupArchive(group)}
                            variant="secondary"
                            style={styles.archiveAction}
                          />
                        ) : null}
                      </Pressable>
                      {group.items.map((draft) => (
                        <View key={draft.id} style={styles.inboxRow}>
                          <View style={styles.inboxRowHead}>
                            <Text style={styles.inboxTitle}>{getDocumentTypeLabel(draft.document_type)}</Text>
                            <StatusPill text={getStatusLabel(draft.status)} tone={tone(draft.status)} />
                          </View>
                          <Text style={styles.metaText}>
                            {draft.structured_fields?.standard_form?.name
                              ? `标准模板：${draft.structured_fields.standard_form.name}`
                              : "标准模板：结构化护理文书"}
                          </Text>
                          <Text style={styles.inboxText} numberOfLines={5}>
                            {formatAiText(draft.draft_text)}
                          </Text>
                          <Text style={styles.metaText}>{new Date(draft.updated_at).toLocaleString()}</Text>
                          <View style={styles.actionRow}>
                            <ActionButton label="打开专业编辑页" onPress={() => openEditor(draft)} variant="secondary" style={styles.smallAction} disabled={busy} />
                            {draft.status === "draft" ? (
                              <ActionButton label="审核" onPress={() => reviewDraft(draft.id)} style={styles.smallAction} disabled={busy} />
                            ) : null}
                            <ActionButton label="归档" onPress={() => submitDraft(draft.id)} style={styles.smallAction} disabled={busy} />
                          </View>
                        </View>
                      ))}
                    </View>
                  ))}

                  {unresolvedDraftGroups.length ? (
                    <View style={styles.archiveGroup}>
                      <Text style={styles.archiveGroupTitle}>待补归档信息</Text>
                      <Text style={styles.emptyHint}>这些草稿还没有可靠的床位、患者姓名或病案号，请先补齐信息，再审核归档。</Text>
                      {unresolvedDraftGroups.flatMap((group) => group.items).map((draft) => (
                        <View key={draft.id} style={styles.inboxRow}>
                          <View style={styles.inboxRowHead}>
                            <Text style={styles.inboxTitle}>{getDocumentTypeLabel(draft.document_type)}</Text>
                            <StatusPill text={getStatusLabel(draft.status)} tone={tone(draft.status)} />
                          </View>
                          <Text style={styles.metaText}>请先在编辑器里补床位、患者姓名和病案号，再进入审核与归档。</Text>
                          <Text style={styles.inboxText} numberOfLines={4}>
                            {formatAiText(draft.draft_text)}
                          </Text>
                          <View style={styles.actionRow}>
                            <ActionButton label="补齐并进入编辑页" onPress={() => openEditor(draft)} variant="secondary" style={styles.smallAction} disabled={busy} />
                          </View>
                        </View>
                      ))}
                    </View>
                  ) : null}

                  {!loading && !resolvedDraftGroups.length && !unresolvedDraftGroups.length ? (
                    <Text style={styles.emptyHint}>当前没有命中的文书草稿，可以直接在智能协作里下达文书指令。</Text>
                  ) : null}
                </PanelSection>
              </>
            ) : null}

            {false && inboxTab === "templates" ? (
              <>
                <PanelSection title="模板导入" action={<ActionButton label="导入模板" onPress={importTemplate} disabled={busy} style={styles.smallAction} />}>
                  <Text style={styles.metaText}>
                    已内置体温单、病重护理记录、输血护理记录、血糖单、交接班等标准模板，并接入 openEHR / HL7 风格结构化表单映射。
                  </Text>
                </PanelSection>

                <PanelSection title="模板库">
                  {filteredTemplates.map((template) => (
                    <View key={template.id} style={styles.inboxRow}>
                      <View style={styles.inboxRowHead}>
                        <Text style={styles.inboxTitle}>{template.name}</Text>
                        <StatusPill
                          text={template.document_type ? getDocumentTypeLabel(template.document_type) : getSourceTypeLabel(template.source_type)}
                          tone="info"
                        />
                      </View>
                      <Text style={styles.metaText}>
                        {(template.trigger_keywords || []).slice(0, 4).join(" / ") || "系统模板"}
                      </Text>
                      <Text style={styles.inboxText} numberOfLines={4}>
                        {template.template_text}
                      </Text>
                      <Text style={styles.metaText}>{new Date(template.updated_at).toLocaleString()}</Text>
                    </View>
                  ))}
                  {!loading && !filteredTemplates.length ? <Text style={styles.emptyHint}>当前没有命中的模板。</Text> : null}
                </PanelSection>
              </>
            ) : null}
          </ScrollView>
        </>
      ) : null}
    </ScreenShell>
  );
}

const styles = StyleSheet.create({
  refreshButton: {
    minWidth: 76,
  },
  errorText: {
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
  segmentRow: {
    flexDirection: "row",
    gap: 8,
  },
  segmentChip: {
    flex: 1,
    minHeight: 40,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
  },
  segmentChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  segmentText: {
    color: colors.subText,
    fontSize: 13,
    fontWeight: "700",
  },
  segmentTextActive: {
    color: "#ffffff",
  },
  searchInput: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  panelSection: {
    gap: 8,
  },
  panelSectionHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  panelSectionTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
    lineHeight: 22,
  },
  visualHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
    flexWrap: "wrap",
  },
  visualHeaderText: {
    flex: 1,
    minWidth: 0,
    gap: 4,
  },
  visualHint: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  visualChipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  visualChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  visualChipActive: {
    backgroundColor: "#d9e8ff",
    borderColor: "#a9c9ff",
  },
  visualChipText: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  visualChipTextActive: {
    color: colors.primary,
  },
  visualDeck: {
    overflow: "hidden",
    paddingTop: 4,
  },
  visualPage: {
    paddingTop: 2,
    paddingBottom: 4,
  },
  visualDotRow: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 8,
  },
  visualDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#d5deea",
  },
  visualDotActive: {
    width: 18,
    backgroundColor: colors.primary,
  },
  flatPanel: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
  },
  emptyActionStack: {
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 14,
  },
  listRow: {
    minHeight: 76,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
  },
  rowLeading: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: "#eef4fb",
    alignItems: "center",
    justifyContent: "center",
  },
  rowLeadingText: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "800",
  },
  rowBody: {
    flex: 1,
    minWidth: 0,
    gap: 4,
  },
  rowHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  rowTitle: {
    flex: 1,
    color: colors.text,
    fontSize: 14.5,
    fontWeight: "700",
    lineHeight: 20,
  },
  rowTrailing: {
    color: colors.subText,
    fontSize: 11.5,
  },
  rowSubLine: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
  },
  rowSubtitle: {
    flex: 1,
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  inlineBadge: {
    alignSelf: "flex-start",
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: "#eef4fb",
  },
  inlineBadgeText: {
    color: colors.primary,
    fontSize: 11.5,
    fontWeight: "700",
  },
  unreadBadge: {
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    paddingHorizontal: 6,
    backgroundColor: "#f43f5e",
    alignItems: "center",
    justifyContent: "center",
  },
  unreadText: {
    color: "#ffffff",
    fontSize: 11,
    fontWeight: "800",
  },
  emptyHint: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
    paddingHorizontal: 14,
    paddingVertical: 14,
  },
  metaText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  friendRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  friendInput: {
    flex: 1,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 11,
  },
  friendButton: {
    minWidth: 84,
  },
  metricRow: {
    flexDirection: "row",
    gap: 10,
  },
  metricBox: {
    flex: 1,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    paddingHorizontal: 12,
    paddingVertical: 12,
    gap: 4,
  },
  metricLabel: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  metricValue: {
    color: colors.primary,
    fontSize: 21,
    fontWeight: "800",
  },
  inboxContent: {
    gap: 12,
    paddingBottom: 8,
  },
  inboxRow: {
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
    gap: 6,
  },
  inboxRowHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  inboxTitle: {
    flex: 1,
    color: colors.text,
    fontSize: 13.8,
    fontWeight: "800",
  },
  inboxText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 20,
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 2,
  },
  smallAction: {
    minWidth: 82,
  },
  archiveGroup: {
    borderTopWidth: 1,
    borderTopColor: "#edf2f5",
  },
  draftMetricRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 4,
    marginBottom: 6,
  },
  draftMetricCard: {
    flex: 1,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#f8fbff",
    paddingVertical: 12,
    paddingHorizontal: 10,
    alignItems: "center",
    gap: 4,
  },
  draftMetricValue: {
    color: colors.primary,
    fontSize: 18,
    fontWeight: "800",
  },
  draftMetricLabel: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  archiveGroupHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    backgroundColor: "#f8fafb",
  },
  archiveGroupTitle: {
    flex: 1,
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  archiveAction: {
    minWidth: 84,
    minHeight: 36,
  },
  archiveEntryPanel: {
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
  },
  archiveEntryRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
    backgroundColor: "#ffffff",
  },
  archiveEntryLeading: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: "#eef4fb",
    alignItems: "center",
    justifyContent: "center",
  },
  archiveEntryLeadingText: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "800",
  },
  archiveEntryBody: {
    flex: 1,
    gap: 4,
  },
  archiveEntryTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  archiveEntrySubtitle: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 17,
  },
  archiveEntryRight: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  archiveEntryCount: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "800",
  },
  riskHeatGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    padding: 14,
  },
  riskHeatCell: {
    width: "48%",
    borderRadius: 16,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 12,
    gap: 4,
  },
  riskHeatHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  riskToneDanger: {
    backgroundColor: "#fff1f1",
    borderColor: "#efb0b0",
  },
  riskToneWarning: {
    backgroundColor: "#fff7ec",
    borderColor: "#f2cb97",
  },
  riskToneInfo: {
    backgroundColor: "#eef7ff",
    borderColor: "#b8d5ee",
  },
  riskHeatBed: {
    color: colors.primary,
    fontSize: 15,
    fontWeight: "800",
  },
  bedColorPlate: {
    minWidth: 58,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  bedColorPlateText: {
    fontSize: 15,
    fontWeight: "800",
  },
  riskHeatName: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  riskHeatMeta: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 17,
  },
  timelineRow: {
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
  },
  timelineTimeWrap: {
    width: 72,
    alignItems: "center",
    gap: 6,
  },
  timelineTime: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "800",
    textAlign: "center",
  },
  timelineDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  timelineDotWarning: {
    backgroundColor: "#d97706",
  },
  timelineDotInfo: {
    backgroundColor: colors.primary,
  },
  timelineDotSuccess: {
    backgroundColor: "#15803d",
  },
  timelineBody: {
    flex: 1,
    gap: 4,
  },
  timelineTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  timelineSubtitle: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  boardRow: {
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
  },
  boardBody: {
    gap: 4,
  },
  boardTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  boardSummary: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  todoRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#edf2f5",
  },
  todoBody: {
    flex: 1,
    gap: 4,
  },
  todoTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  todoSubtitle: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  pickerChip: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 4,
  },
  pickerChipActive: {
    borderColor: "#a9c9ff",
    backgroundColor: "#edf4ff",
  },
  pickerChipLabel: {
    color: colors.text,
    fontSize: 12.5,
    fontWeight: "700",
  },
  pickerChipLabelActive: {
    color: colors.primary,
  },
  pickerChipNote: {
    color: colors.subText,
    fontSize: 11.5,
    lineHeight: 16,
  },
  pickerChipNoteActive: {
    color: colors.primary,
  },
  templateWorkbench: {
    gap: 12,
  },
  templateWorkbenchWide: {
    flexDirection: "row",
    alignItems: "flex-start",
    columnGap: 14,
  },
  templateCatalogColumn: {
    flex: 1,
    minWidth: 0,
    gap: 10,
  },
  templateDetailColumn: {
    flex: 1.2,
    minWidth: 0,
    gap: 10,
  },
  templateFlowGrid: {
    gap: 12,
    marginTop: 4,
    minWidth: 0,
  },
  templateFlowGridWide: {
    flexDirection: "column",
    alignItems: "stretch",
  },
  templateActionGrid: {
    gap: 12,
    marginTop: 6,
    minWidth: 0,
  },
  templateActionGridWide: {
    flexDirection: "row",
    flexWrap: "nowrap",
    alignItems: "stretch",
  },
  templateCard: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 14,
    gap: 6,
  },
  templateCardActive: {
    borderColor: "#8fb8ff",
    backgroundColor: "#f5f9ff",
  },
  templateCardHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  templateCardTitle: {
    flex: 1,
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  templateDetailCard: {
    gap: 10,
    overflow: "hidden",
    minWidth: 0,
  },
  templateFlowCard: {
    width: "100%",
    minWidth: 0,
  },
  templatePreviewCard: {
    width: "100%",
    minWidth: 0,
  },
  templateActionCard: {
    flex: 1,
    minWidth: 0,
  },
  templateDetailTitle: {
    flex: 1,
    color: colors.primary,
    fontSize: 15,
    fontWeight: "800",
  },
  templateDetailLabel: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  templateDetailMeta: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  templateChipGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    paddingBottom: 4,
  },
  templateBedPicker: {
    height: 220,
    maxHeight: 320,
  },
  templateBedPickerWide: {
    height: 280,
  },
  templatePickerRelaxed: {
    height: undefined,
    maxHeight: undefined,
  },
  templateArchivePicker: {
    height: 360,
    maxHeight: 420,
  },
  templateArchivePickerWide: {
    height: 520,
    maxHeight: 560,
  },
  templateBodyPreview: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#fbfdff",
    minHeight: 360,
    maxHeight: 520,
    overflow: "hidden",
  },
  templateBodyPreviewRelaxed: {
    minHeight: 0,
    maxHeight: undefined,
  },
  templateBodyPreviewContent: {
    paddingHorizontal: 12,
    paddingTop: 12,
    paddingBottom: 16,
  },
  templateBodyText: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 20,
  },
  templateNotesInput: {
    minHeight: 112,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
});
