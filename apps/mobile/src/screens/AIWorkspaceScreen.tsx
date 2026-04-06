import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation } from "@react-navigation/native";

import { api, getApiErrorMessage } from "../api/endpoints";
import { AppGlyph } from "../components/AppGlyph";
import { ActionButton, StatusPill } from "../components/ui";
import { VoiceTextInput } from "../components/VoiceTextInput";
import { useAppStore } from "../store/appStore";
import { colors } from "../theme";
import type {
  AIChatMessage,
  AIChatMode,
  AIExecutionProfile,
  AIModelsCatalog,
  AIRuntimeStatus,
  ConversationHistoryItem,
  PatientScopePreview,
} from "../types";
import {
  buildChatSessionTitle,
  loadChatSessions,
  saveChatSessions,
  type ChatSessionRecord,
  upsertChatSession,
} from "../utils/chatSessionStore";
import { buildAiOperatorNotes, buildSessionMemory, cleanMemoryList, cleanMemorySummaryText } from "../utils/chatMemory";
import {
  getClusterLabel,
  getDepartmentLabel,
  getExecutionProfileLabel,
  getModeLabel,
  getModelLabel,
} from "../utils/displayText";
import { compactText, formatBedLabel, normalizePersonName } from "../utils/displayValue";
import { formatAiText } from "../utils/text";

const PROFILE_META: Record<AIExecutionProfile, { label: string; summary: string }> = {
  observe: { label: "快速观察", summary: "偏向问答、风险判断和提醒" },
  escalate: { label: "沟通上报", summary: "偏向交班、上报和对齐要点" },
  document: { label: "文书处理", summary: "偏向护理文书和模板生成" },
  full_loop: { label: "持续跟进", summary: "偏向持续推进任务与多步执行" },
};

const QUICK_PROMPTS = [
  "中医辨证在这里怎么实现",
  "帮我看12床现在要重点注意什么",
  "同时分析12床、15床和16床的风险",
  "生成今天全病区交接班草稿",
];

function buildPreviewSummary(preview: PatientScopePreview | null, input: string) {
  if (!input.trim()) {
    return "可直接问通用护理问题，也可以提床号、多床或病区。";
  }
  if (!preview) {
    return "正在识别是不是病例问题。";
  }
  if (!preview.matched_patients.length) {
    if (preview.extracted_beds.length) {
      return `识别到床号 ${preview.extracted_beds.join("、")}，但当前没有命中具体病例。`;
    }
    return "这是通用问题，会直接回答，不强制挂患者上下文。";
  }
  if (preview.ward_scope || preview.matched_patients.length > 1) {
    return `已识别为多病例范围：${preview.matched_patients
      .map((item) => formatBedLabel(item.bed_no || item.requested_bed_no, "未定位"))
      .filter(Boolean)
      .join("、")}。`;
  }
  const item = preview.matched_patients[0];
  return `已定位 ${formatBedLabel(item.bed_no || item.requested_bed_no, "未定位")}${item.patient_name ? ` · ${normalizePersonName(item.patient_name)}` : ""}。`;
}

function formatBubbleMeta(message: AIChatMessage) {
  const scope = message.response?.bed_no ? formatBedLabel(message.response.bed_no) : "通用";
  return `${message.role === "user" ? "你" : "智能协作"} · ${scope}`;
}

function cleanAssistantSummary(value: string) {
  return formatAiText(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(
      (line) =>
        line &&
        !/^系统已经先帮你完成一轮梳理/.test(line) &&
        !/^已参考历史记忆[:：]/.test(line) &&
        !/^会话摘要[:：]/.test(line) &&
        !/^已记住[:：]/.test(line) &&
        !/^待继续[:：]/.test(line)
    )
    .join("\n")
    .trim();
}

function buildAssistantBubbleText(message: AIChatMessage) {
  const response = message.response;
  const summary = cleanAssistantSummary(response?.summary || message.text || "");
  const findings = Array.isArray(response?.findings)
    ? response.findings.map((item) => formatAiText(item).trim()).filter(Boolean)
    : [];
  const recommendations = Array.isArray(response?.recommendations)
    ? response.recommendations
        .slice()
        .sort((a, b) => a.priority - b.priority)
        .map((item) => formatAiText(item.title).trim())
        .filter(Boolean)
    : [];
  const nextActions = Array.isArray(response?.next_actions)
    ? response.next_actions.map((item) => formatAiText(item).trim()).filter(Boolean)
    : [];

  const parts: string[] = [];
  if (summary) {
    parts.push(summary);
  }
  if (findings.length) {
    parts.push("", "观察重点：", ...findings.slice(0, 4).map((item) => `• ${item}`));
  }
  if (recommendations.length) {
    parts.push("", "建议动作：", ...recommendations.slice(0, 4).map((item, index) => `${index + 1}. ${item}`));
  }
  if (nextActions.length) {
    parts.push("", "后续提醒：", ...nextActions.slice(0, 3).map((item) => `• ${item}`));
  }
  return parts.join("\n").trim();
}

function buildHistorySessions(items: ConversationHistoryItem[]): ChatSessionRecord[] {
  const map = new Map<string, ChatSessionRecord>();

  items
    .slice()
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
    .forEach((item) => {
      const conversationId = item.conversation_id || item.id;
      const mode: AIChatMode = item.workflow_type === "single_model_chat" ? "single_model" : "agent_cluster";
      const executionProfile =
        item.execution_profile === "observe" ||
        item.execution_profile === "escalate" ||
        item.execution_profile === "document" ||
        item.execution_profile === "full_loop"
          ? item.execution_profile
          : undefined;
      const existing = map.get(conversationId);
      const messages = existing?.messages ? [...existing.messages] : [];

      if (item.user_input) {
        messages.push({
          id: `${item.id}-u`,
          role: "user",
          mode,
          text: item.user_input,
          timestamp: item.created_at,
        });
      }

      messages.push({
        id: `${item.id}-a`,
        role: "assistant",
        mode,
        text: item.summary,
        timestamp: item.created_at,
        response: {
          mode,
          workflow_type: item.workflow_type as any,
          summary: item.summary,
          findings: item.findings || [],
          recommendations: item.recommendations || [],
          confidence: item.confidence || 0.7,
          review_required: Boolean(item.review_required),
          steps: item.steps || [],
          model_plan: [],
          agent_mode: item.agent_mode || "assisted",
          execution_profile: executionProfile,
          mission_title: item.mission_title,
          success_criteria: item.success_criteria || [],
          plan: item.plan || [],
          memory: item.memory,
          artifacts: item.artifacts || [],
          specialist_profiles: item.specialist_profiles || [],
          hybrid_care_path: item.hybrid_care_path || [],
          data_capsule: item.data_capsule,
          health_graph: item.health_graph,
          reasoning_cards: item.reasoning_cards || [],
          pending_approvals: item.pending_approvals || [],
          next_actions: item.next_actions || [],
          run_id: item.run_id,
          runtime_engine: item.runtime_engine,
          patient_id: item.patient_id,
          created_at: item.created_at,
          patient_name: undefined,
          bed_no: undefined,
        },
      });

      map.set(conversationId, {
        id: conversationId,
        conversationId,
        title: existing?.title || buildChatSessionTitle(item.user_input || item.summary),
        mode,
        selectedModel: existing?.selectedModel,
        clusterProfile: existing?.clusterProfile,
        executionProfile: executionProfile || existing?.executionProfile,
        createdAt: existing?.createdAt || item.created_at,
        updatedAt: item.created_at,
        lastPrompt: item.user_input || existing?.lastPrompt || "",
        lastSummary: item.summary,
        ...buildSessionMemory(messages),
        messages,
      });
    });

  return Array.from(map.values()).sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
}

function formatSessionTime(value: string) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    return "";
  }
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function AIWorkspaceScreen() {
  const navigation = useNavigation<any>();
  const { width } = useWindowDimensions();
  const isWide = width >= 980;
  const user = useAppStore((state) => state.user);
  const departmentId = useAppStore((state) => state.selectedDepartmentId);

  const [catalog, setCatalog] = useState<AIModelsCatalog | null>(null);
  const [runtime, setRuntime] = useState<AIRuntimeStatus | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [mode, setMode] = useState<AIChatMode>("agent_cluster");
  const [selectedModel, setSelectedModel] = useState("minicpm3_4b_local");
  const [clusterProfile, setClusterProfile] = useState("nursing_default_cluster");
  const [executionProfile, setExecutionProfile] = useState<AIExecutionProfile>("observe");
  const [conversationId, setConversationId] = useState("");
  const [composerText, setComposerText] = useState("");
  const [scopePreview, setScopePreview] = useState<PatientScopePreview | null>(null);
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSessionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [error, setError] = useState("");
  const [expandedMessages, setExpandedMessages] = useState<Record<string, boolean>>({});
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chatScrollRef = useRef<ScrollView | null>(null);
  const shouldAutoScrollRef = useRef(true);

  const selectedProfile = catalog?.cluster_profiles.find((item) => item.id === clusterProfile) || catalog?.cluster_profiles[0];
  const selectedModelInfo = catalog?.single_models.find((item) => item.id === selectedModel) || catalog?.single_models[0];
  const previewSummary = useMemo(() => buildPreviewSummary(scopePreview, composerText), [scopePreview, composerText]);
  const runtimeUnavailable = Boolean(runtime) && !runtime?.local_model_service_reachable;
  const activeSession = useMemo(
    () => sessions.find((item) => item.conversationId === conversationId) || null,
    [conversationId, sessions]
  );
  const memoryFacts = useMemo(
    () =>
      cleanMemoryList(activeSession?.memoryFacts || [], {
        bedNo: activeSession?.lastBedNo,
        patientName: activeSession?.lastPatientName,
      }, 3),
    [activeSession?.lastBedNo, activeSession?.lastPatientName, activeSession?.memoryFacts]
  );
  const memoryTodos = useMemo(
    () =>
      cleanMemoryList(activeSession?.memoryTodos || [], {
        bedNo: activeSession?.lastBedNo,
        patientName: activeSession?.lastPatientName,
      }, 3),
    [activeSession?.lastBedNo, activeSession?.lastPatientName, activeSession?.memoryTodos]
  );
  const memorySummary = useMemo(() => cleanMemorySummaryText(activeSession?.memorySummary || ""), [activeSession?.memorySummary]);
  const runtimeWarningText = runtimeUnavailable
    ? "本地模型服务未启动，当前只能走兜底流程。通用问题可能变得不够智能，复杂任务也会更慢。"
    : "";
  const showRuntimeWarning = runtimeUnavailable && isWide;

  useEffect(() => {
    if (isWide) {
      setDrawerOpen(false);
    }
  }, [isWide]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoadingCatalog(true);
      try {
        const [nextCatalog, nextRuntime, localSessions] = await Promise.all([
          api.getAiModels(),
          api.getAiRuntimeStatus(),
          loadChatSessions(),
        ]);

        let nextSessions = localSessions;
        if (!nextSessions.length && user?.id) {
          try {
            nextSessions = buildHistorySessions(await api.listWorkflowHistory({ requestedBy: user.id, limit: 60 }));
          } catch {
            nextSessions = [];
          }
        }

        if (cancelled) {
          return;
        }

        setCatalog(nextCatalog);
        setRuntime(nextRuntime);
        setSessions(nextSessions);

        if (nextCatalog.single_models.length && !nextCatalog.single_models.some((item) => item.id === selectedModel)) {
          setSelectedModel(nextCatalog.single_models[0].id);
        }
        if (nextCatalog.cluster_profiles.length && !nextCatalog.cluster_profiles.some((item) => item.id === clusterProfile)) {
          setClusterProfile(nextCatalog.cluster_profiles[0].id);
        }

      } catch (err) {
        if (!cancelled) {
          setError(getApiErrorMessage(err, "智能配置加载失败，请稍后重试。"));
        }
      } finally {
        if (!cancelled) {
          setLoadingCatalog(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  useEffect(() => {
    let cancelled = false;

    const refreshRuntime = async () => {
      try {
        const nextRuntime = await api.getAiRuntimeStatus();
        if (!cancelled) {
          setRuntime(nextRuntime);
        }
      } catch {
        // ??????????????????
      }
    };

    const timer = setInterval(() => {
      void refreshRuntime();
    }, 25000);

    void refreshRuntime();

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const trimmed = composerText.trim();
    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
    }
    if (!trimmed) {
      setScopePreview(null);
      return;
    }

    previewTimerRef.current = setTimeout(async () => {
      try {
        setScopePreview(await api.previewPatientScope({ userInput: trimmed, departmentId, requestedBy: user?.id }));
      } catch {
        setScopePreview(null);
      }
    }, 320);

    return () => {
      if (previewTimerRef.current) {
        clearTimeout(previewTimerRef.current);
      }
    };
  }, [composerText, departmentId, user?.id]);

  useEffect(() => {
    if (!shouldAutoScrollRef.current) {
      return undefined;
    }
    const timer = setTimeout(() => {
      chatScrollRef.current?.scrollToEnd({ animated: true });
    }, 60);
    return () => clearTimeout(timer);
  }, [messages]);

  const persistSession = async (next: ChatSessionRecord) => {
    const merged = upsertChatSession(sessions, next);
    setSessions(merged);
    await saveChatSessions(merged);
  };

  const restoreSession = (session: ChatSessionRecord) => {
    shouldAutoScrollRef.current = true;
    setConversationId(session.conversationId);
    setMessages(session.messages);
    setMode(session.mode);
    setSelectedModel(session.selectedModel || "minicpm3_4b_local");
    setClusterProfile(session.clusterProfile || "nursing_default_cluster");
    setExecutionProfile(session.executionProfile || "observe");
    setComposerText("");
    setScopePreview(null);
    setError("");
    if (!isWide) {
      setDrawerOpen(false);
    }
  };

  const resetConversation = () => {
    shouldAutoScrollRef.current = true;
    setConversationId("");
    setComposerText("");
    setScopePreview(null);
    setMessages([]);
    setError("");
  };

  const sendMessage = async () => {
    const text = composerText.trim();
    if (!text || loading) {
      return;
    }

    const nextConversationId = conversationId || `chat-${Date.now()}`;
    const userMessage: AIChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      mode,
      text,
      timestamp: new Date().toISOString(),
    };

    const previousMessages = messages;
    const optimisticMessages = [...messages, userMessage];

    shouldAutoScrollRef.current = true;
    setConversationId(nextConversationId);
    setMessages(optimisticMessages);
    setComposerText("");
    setError("");
    setLoading(true);

    try {
      const response = await api.runAiChat({
        mode,
        selectedModel,
        clusterProfile,
        conversationId: nextConversationId,
        departmentId,
        userInput: text,
        requestedBy: user?.id,
        executionProfile: mode === "agent_cluster" ? executionProfile : undefined,
        operatorNotes:
          mode === "agent_cluster"
            ? buildAiOperatorNotes(buildSessionMemory(messages), runtimeUnavailable ? ["若本地模型未启动，请优先返回核心结论和待办。"] : undefined)
            : undefined,
      });

      const assistantMessage: AIChatMessage = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        mode,
        text: response.summary,
        timestamp: response.created_at,
        response,
      };

      const finalMessages = [...optimisticMessages, assistantMessage];
      setMessages(finalMessages);

      setScopePreview(
        response.bed_no || response.patient_name
          ? {
              question: text,
              department_id: departmentId,
              ward_scope: false,
              global_scope: false,
              extracted_beds: response.bed_no ? [response.bed_no] : [],
              unresolved_beds: [],
              matched_patients: [
                {
                  patient_id: response.patient_id,
                  patient_name: response.patient_name,
                  bed_no: response.bed_no,
                  diagnoses: [],
                  risk_tags: [],
                  pending_tasks: [],
                  bed_no_corrected: false,
                },
              ],
            }
          : null
      );

      await persistSession({
        id: nextConversationId,
        conversationId: nextConversationId,
        title: buildChatSessionTitle(text),
        mode,
        selectedModel,
        clusterProfile,
        executionProfile,
        createdAt: sessions.find((item) => item.id === nextConversationId)?.createdAt || userMessage.timestamp,
        updatedAt: response.created_at,
        lastPrompt: text,
        lastSummary: response.summary,
        ...buildSessionMemory(finalMessages, response),
        messages: finalMessages,
      });
    } catch (err) {
      setMessages(previousMessages);
      setComposerText(text);
      setError(getApiErrorMessage(err, "智能处理失败，请稍后重试。"));
      try {
        setRuntime(await api.getAiRuntimeStatus());
      } catch {
        // ???????????????????
      }
    } finally {
      setLoading(false);
    }
  };

  const drawerPanel = (
    <View style={styles.drawerPanel}>
      <View style={styles.drawerHeader}>
        <Text style={styles.drawerTitle}>工作台设置</Text>
        {!isWide ? (
          <Pressable style={styles.squareButton} onPress={() => setDrawerOpen(false)}>
            <AppGlyph name="close" />
          </Pressable>
        ) : null}
      </View>

      <View style={styles.drawerSection}>
        <Text style={styles.drawerSectionTitle}>模式</Text>
        <View style={styles.modeRow}>
          <Pressable style={[styles.modeChip, mode === "single_model" && styles.modeChipActive]} onPress={() => setMode("single_model")}>
            <Text style={[styles.modeChipText, mode === "single_model" && styles.modeChipTextActive]}>单模型</Text>
          </Pressable>
          <Pressable style={[styles.modeChip, mode === "agent_cluster" && styles.modeChipActive]} onPress={() => setMode("agent_cluster")}>
            <Text style={[styles.modeChipText, mode === "agent_cluster" && styles.modeChipTextActive]}>智能协作</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.drawerSection}>
        <Text style={styles.drawerSectionTitle}>{mode === "single_model" ? "模型" : "工作流"}</Text>
        {(mode === "single_model" ? catalog?.single_models || [] : catalog?.cluster_profiles || []).map((item: any) => {
          const active = mode === "single_model" ? selectedModel === item.id : clusterProfile === item.id;
          return (
            <Pressable
              key={item.id}
              style={[styles.drawerListRow, active && styles.drawerListRowActive]}
              onPress={() => (mode === "single_model" ? setSelectedModel(item.id) : setClusterProfile(item.id))}
            >
              <View style={{ flex: 1 }}>
                <Text style={[styles.drawerRowTitle, active && styles.drawerRowTitleActive]}>
                  {mode === "single_model" ? getModelLabel(item.id) : getClusterLabel(item.id)}
                </Text>
                <Text style={styles.drawerRowSubtitle} numberOfLines={2}>
                  {mode === "single_model" ? "用于通用护理问答和基础文书辅助" : "用于多步协同、病区分析和文书联动"}
                </Text>
              </View>
              <AppGlyph name="chevron" color={active ? colors.primary : colors.subText} />
            </Pressable>
          );
        })}
      </View>

      {mode === "agent_cluster" ? (
        <View style={styles.drawerSection}>
          <Text style={styles.drawerSectionTitle}>执行策略</Text>
          {(Object.keys(PROFILE_META) as AIExecutionProfile[]).map((key) => {
            const active = executionProfile === key;
            return (
              <Pressable key={key} style={[styles.drawerListRow, active && styles.drawerListRowActive]} onPress={() => setExecutionProfile(key)}>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.drawerRowTitle, active && styles.drawerRowTitleActive]}>{PROFILE_META[key].label}</Text>
                  <Text style={styles.drawerRowSubtitle}>{PROFILE_META[key].summary}</Text>
                </View>
                <AppGlyph name="chevron" color={active ? colors.primary : colors.subText} />
              </Pressable>
            );
          })}
        </View>
      ) : null}

      <View style={styles.drawerSection}>
        <Text style={styles.drawerSectionTitle}>最近对话</Text>
        {sessions.slice(0, 8).map((session) => {
          const active = session.conversationId === conversationId;
          return (
            <Pressable key={session.id} style={[styles.drawerListRow, active && styles.drawerListRowActive]} onPress={() => restoreSession(session)}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.drawerRowTitle, active && styles.drawerRowTitleActive]} numberOfLines={1}>
                  {session.title}
                </Text>
                <Text style={styles.drawerRowSubtitle}>
                  {getModeLabel(session.mode)} · {formatSessionTime(session.updatedAt)}
                </Text>
                {(session.lastBedNo || session.memorySummary) ? (
                  <Text style={styles.drawerRowHint} numberOfLines={2}>
                    {[
                        session.lastBedNo ? formatBedLabel(session.lastBedNo) : "",
                        session.lastPatientName ? normalizePersonName(session.lastPatientName) : "",
                        compactText(cleanMemorySummaryText(session.memorySummary), 54),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Text>
                ) : null}
              </View>
              <AppGlyph name="history" color={active ? colors.primary : colors.subText} />
            </Pressable>
          );
        })}
        {!sessions.length ? <Text style={styles.drawerEmpty}>还没有历史对话。</Text> : null}
      </View>
    </View>
  );

  const runtimeLabel = runtime?.active_engine === "langgraph" ? "深度编排" : "标准模式";
  const summaryLine =
    mode === "single_model"
      ? `${selectedModelInfo?.name || "单模型"} · ${departmentId}`
      : `${selectedProfile?.name || "系统协同"} · ${PROFILE_META[executionProfile].label} · ${departmentId}`;

  const showMetaPanels = isWide;

  return (
    <SafeAreaView style={styles.safe} edges={["top", "left", "right", "bottom"]}>
      {!isWide ? (
        <Modal visible={drawerOpen} transparent animationType="fade" onRequestClose={() => setDrawerOpen(false)}>
          <View style={styles.modalBackdrop}>
            <Pressable style={styles.modalMask} onPress={() => setDrawerOpen(false)} />
            <View style={styles.modalSheet}>{drawerPanel}</View>
          </View>
        </Modal>
      ) : null}

      <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={[styles.header, !isWide && styles.headerCompact]}>
          <View style={styles.headerRow}>
            <Pressable style={styles.squareButton} onPress={() => setDrawerOpen(true)}>
              <AppGlyph name="menu" />
            </Pressable>
            <View style={styles.headerTextWrap}>
              <Text style={styles.headerTitle}>临床智能工作台</Text>
              {isWide ? <Text style={styles.headerSubtitle}>一个入口完成问答、病例分析、文书沉淀和历史对话</Text> : null}
            </View>
          </View>
          <View style={[styles.headerActions, !isWide && styles.headerActionsCompact]}>
            <ActionButton
              label="收件箱"
              onPress={() => navigation.navigate("Tasks")}
              variant="secondary"
              style={[styles.headerButton, !isWide && styles.headerButtonCompact]}
            />
            <ActionButton
              label="新对话"
              onPress={resetConversation}
              variant="secondary"
              style={[styles.headerButton, !isWide && styles.headerButtonCompact]}
            />
          </View>
        </View>

        <View style={styles.body}>
          {isWide ? <View style={styles.desktopDrawer}>{drawerPanel}</View> : null}

          <View style={[styles.workspace, !isWide && styles.workspaceCompact]}>
            <View style={styles.topStrip}>
              <View style={styles.modeStrip}>
                <Pressable style={[styles.topChip, mode === "agent_cluster" && styles.topChipActive]} onPress={() => setMode("agent_cluster")}>
                  <Text style={[styles.topChipText, mode === "agent_cluster" && styles.topChipTextActive]}>智能协作</Text>
                </Pressable>
                <Pressable style={[styles.topChip, mode === "single_model" && styles.topChipActive]} onPress={() => setMode("single_model")}>
                  <Text style={[styles.topChipText, mode === "single_model" && styles.topChipTextActive]}>单模型</Text>
                </Pressable>
              </View>
              <StatusPill text={runtimeLabel} tone={runtime?.active_engine === "langgraph" ? "success" : "info"} />
            </View>

            {showRuntimeWarning ? (
              <View style={styles.runtimeWarning}>
                <Text style={styles.runtimeWarningTitle}>模型未启动</Text>
                <Text style={styles.runtimeWarningText}>{runtimeWarningText}</Text>
              </View>
            ) : null}

            {showMetaPanels ? <View style={styles.contextStrip}>
              <Text style={styles.contextTitle}>
                {mode === "single_model"
                  ? `${getModelLabel(selectedModelInfo?.id || selectedModel)} · ${getDepartmentLabel(departmentId)}`
                  : `${getClusterLabel(selectedProfile?.id || clusterProfile)} · ${getExecutionProfileLabel(executionProfile)} · ${getDepartmentLabel(
                      departmentId
                    )}`}
              </Text>
              <Text style={styles.contextText}>{previewSummary}</Text>
            </View> : null}

            {showMetaPanels && (memorySummary || memoryFacts.length || memoryTodos.length) ? (
              <View style={styles.memoryStrip}>
                <Text style={styles.memoryTitle}>会话记忆</Text>
                {memorySummary ? <Text style={styles.memoryText}>{memorySummary}</Text> : null}
                {memoryFacts.length ? <Text style={styles.memoryMeta}>已记住：{memoryFacts.slice(0, 3).join("；")}</Text> : null}
                {memoryTodos.length ? <Text style={styles.memoryMeta}>待继续：{memoryTodos.slice(0, 3).join("；")}</Text> : null}
              </View>
            ) : null}

            <View style={styles.chatShell}>
              <ScrollView
                ref={chatScrollRef}
                style={styles.chatScroll}
                contentContainerStyle={[styles.chatContent, !showMetaPanels && styles.chatContentCompact]}
                keyboardShouldPersistTaps="handled"
                keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
                nestedScrollEnabled
                showsVerticalScrollIndicator={!isWide}
                scrollEventThrottle={16}
                onScroll={(event) => {
                  const { layoutMeasurement, contentOffset, contentSize } = event.nativeEvent;
                  const distanceToBottom = contentSize.height - (contentOffset.y + layoutMeasurement.height);
                  shouldAutoScrollRef.current = distanceToBottom < 96;
                }}
              >
                {!messages.length ? (
                  <View style={styles.emptyPanel}>
                    <Text style={styles.emptyTitle}>直接提问就行</Text>
                    <Text style={styles.emptyText}>不用先选病例。问通用护理问题会直接回答，提到床号或病区时再自动定位。</Text>
                    <View style={styles.quickPromptWrap}>
                      {QUICK_PROMPTS.map((item) => (
                        <Pressable key={item} style={styles.quickPromptChip} onPress={() => setComposerText(item)}>
                          <Text style={styles.quickPromptText}>{item}</Text>
                        </Pressable>
                      ))}
                    </View>
                  </View>
                ) : null}

                {messages.map((message) => {
                  const bubbleText = message.role === "assistant" ? buildAssistantBubbleText(message) : message.text;
                  const expandable = bubbleText.length > 260 || bubbleText.split("\n").length > 8;
                  const expanded = !isWide
                    ? true
                    : expandedMessages[message.id] !== undefined
                    ? Boolean(expandedMessages[message.id])
                    : message.role === "assistant";
                  return (
                  <View key={message.id} style={[styles.bubbleWrap, message.role === "user" && styles.bubbleWrapMine]}>
                    <View
                      style={[
                        styles.bubble,
                        !isWide && styles.bubbleCompact,
                        message.role === "user" ? styles.bubbleMine : styles.bubbleAssistant,
                      ]}
                    >
                      {isWide ? (
                        <Text style={[styles.bubbleMeta, message.role === "user" && styles.bubbleMetaMine]}>{formatBubbleMeta(message)}</Text>
                      ) : null}
                      <Text
                        style={[styles.bubbleText, message.role === "user" && styles.bubbleTextMine]}
                        numberOfLines={isWide && expandable && !expanded ? 8 : undefined}
                      >
                        {bubbleText}
                      </Text>
                      {isWide && expandable ? (
                        <Pressable onPress={() => setExpandedMessages((current) => ({ ...current, [message.id]: !expanded }))}>
                          <Text style={[styles.expandText, message.role === "user" && styles.expandTextMine]}>
                            {expanded ? "收起" : "展开全文"}
                          </Text>
                        </Pressable>
                      ) : null}
                    </View>
                  </View>
                )})}
              </ScrollView>
            </View>

            <View style={styles.composerPanel}>
              <VoiceTextInput
                compact
                value={composerText}
                onChangeText={setComposerText}
                onSubmit={sendMessage}
                placeholder="输入问题、床号、病区或护理文书指令"
              />
              {error ? <Text style={styles.errorText}>{error}</Text> : null}
            </View>
          </View>
        </View>

        {loadingCatalog ? (
          <View style={styles.loadingOverlay}>
            <Text style={styles.loadingText}>正在加载智能配置...</Text>
          </View>
        ) : null}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: "#f3f6f8",
  },
  page: {
    flex: 1,
  },
  header: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 12,
    backgroundColor: "#ffffff",
    borderBottomWidth: 1,
    borderBottomColor: "#dbe4ea",
    gap: 10,
  },
  headerCompact: {
    paddingHorizontal: 12,
    paddingTop: 6,
    paddingBottom: 10,
    gap: 8,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  headerTextWrap: {
    flex: 1,
    minWidth: 0,
    gap: 2,
  },
  headerTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "800",
  },
  headerSubtitle: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 17,
  },
  headerActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  headerActionsCompact: {
    flexWrap: "nowrap",
  },
  headerButton: {
    minWidth: 92,
    minHeight: 40,
  },
  headerButtonCompact: {
    minWidth: 0,
    flex: 1,
  },
  squareButton: {
    width: 38,
    height: 38,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#f7f9fa",
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    flexDirection: "row",
    minHeight: 0,
  },
  desktopDrawer: {
    width: 300,
    borderRightWidth: 1,
    borderRightColor: "#dbe4ea",
    backgroundColor: "#ffffff",
  },
  workspace: {
    flex: 1,
    minHeight: 0,
    padding: 14,
    gap: 12,
  },
  workspaceCompact: {
    paddingHorizontal: 10,
    paddingTop: 10,
    paddingBottom: 8,
    gap: 10,
  },
  topStrip: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
  },
  modeStrip: {
    flexDirection: "row",
    gap: 8,
  },
  topChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  topChipActive: {
    borderColor: colors.primary,
    backgroundColor: "#eef4fb",
  },
  topChipText: {
    color: colors.subText,
    fontSize: 12.5,
    fontWeight: "700",
  },
  topChipTextActive: {
    color: colors.primary,
  },
  contextStrip: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 4,
  },
  contextTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "700",
  },
  contextText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  runtimeWarning: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#f2c38a",
    backgroundColor: "#fff7ea",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 4,
  },
  runtimeWarningTitle: {
    color: "#9a5600",
    fontSize: 13.5,
    fontWeight: "800",
  },
  runtimeWarningText: {
    color: "#9a5600",
    fontSize: 12.5,
    lineHeight: 18,
  },
  memoryStrip: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 4,
  },
  memoryTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "800",
  },
  memoryText: {
    color: colors.text,
    fontSize: 12.5,
    lineHeight: 18,
  },
  memoryMeta: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 17,
  },
  chatShell: {
    flex: 1,
    minHeight: 0,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
  },
  chatScroll: {
    flex: 1,
  },
  chatContent: {
    flexGrow: 1,
    padding: 14,
    gap: 12,
  },
  chatContentCompact: {
    paddingTop: 10,
    paddingBottom: 208,
  },
  emptyPanel: {
    gap: 10,
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "800",
  },
  emptyText: {
    color: colors.subText,
    fontSize: 13.5,
    lineHeight: 20,
  },
  quickPromptWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 2,
  },
  quickPromptChip: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#f7f9fa",
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  quickPromptText: {
    color: colors.text,
    fontSize: 12.5,
    fontWeight: "600",
  },
  bubbleWrap: {
    alignItems: "flex-start",
  },
  bubbleWrapMine: {
    alignItems: "flex-end",
  },
  bubble: {
    maxWidth: "88%",
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 6,
  },
  bubbleCompact: {
    maxWidth: "100%",
  },
  bubbleAssistant: {
    backgroundColor: "#f8fafb",
    borderWidth: 1,
    borderColor: "#e1e8ed",
  },
  bubbleMine: {
    backgroundColor: colors.primary,
  },
  bubbleMeta: {
    color: colors.subText,
    fontSize: 11.5,
    fontWeight: "700",
  },
  bubbleMetaMine: {
    color: "rgba(255,255,255,0.75)",
  },
  bubbleText: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
  },
  expandText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "700",
  },
  expandTextMine: {
    color: "#dbeafe",
  },
  bubbleTextMine: {
    color: "#ffffff",
  },
  composerPanel: {
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#dbe4ea",
    backgroundColor: "#ffffff",
    paddingHorizontal: 10,
    paddingVertical: 10,
    gap: 8,
  },
  errorText: {
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
  drawerPanel: {
    flex: 1,
    backgroundColor: "#ffffff",
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 20,
    gap: 14,
  },
  drawerHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  drawerTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "800",
  },
  drawerSection: {
    gap: 8,
  },
  drawerSectionTitle: {
    color: colors.subText,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  drawerListRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 11,
    paddingHorizontal: 12,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#e1e8ed",
    backgroundColor: "#ffffff",
  },
  drawerListRowActive: {
    borderColor: "#cbd9e7",
    backgroundColor: "#eef4fb",
  },
  drawerRowTitle: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "700",
  },
  drawerRowTitleActive: {
    color: colors.primary,
  },
  drawerRowSubtitle: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 2,
  },
  drawerRowHint: {
    color: colors.primary,
    fontSize: 11.5,
    lineHeight: 16,
    marginTop: 4,
  },
  drawerEmpty: {
    color: colors.subText,
    fontSize: 12.5,
  },
  modeRow: {
    flexDirection: "row",
    gap: 8,
  },
  modeChip: {
    flex: 1,
    paddingVertical: 11,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    alignItems: "center",
  },
  modeChipActive: {
    borderColor: colors.primary,
    backgroundColor: "#eef4fb",
  },
  modeChipText: {
    color: colors.subText,
    fontSize: 13,
    fontWeight: "700",
  },
  modeChipTextActive: {
    color: colors.primary,
  },
  modalBackdrop: {
    flex: 1,
    flexDirection: "row",
    backgroundColor: "rgba(15, 23, 42, 0.18)",
  },
  modalMask: {
    flex: 1,
  },
  modalSheet: {
    width: 304,
    backgroundColor: "#ffffff",
  },
  loadingOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(243,246,248,0.7)",
  },
  loadingText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700",
  },
});
