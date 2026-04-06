import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { api, getApiErrorMessage } from "../api/endpoints";
import { StatusPill, SurfaceCard } from "../components/ui";
import type { RootStackParamList } from "../navigation/RootNavigator";
import { useAppStore } from "../store/appStore";
import { colors, spacing } from "../theme";
import type { AIChatMessage, DirectSessionDetail } from "../types";
import {
  buildChatSessionTitle,
  loadChatSessions,
  saveChatSessions,
  upsertChatSession,
  type ChatSessionRecord,
} from "../utils/chatSessionStore";
import { buildAssistantMessageText } from "../utils/aiAssistantText";
import { buildAiOperatorNotes, buildSessionMemory } from "../utils/chatMemory";
import { AI_AGENT_CHAT_SESSION_ID, AI_AGENT_CHAT_TITLE } from "../utils/messageThreads";
import { compactText, formatBedLabel, normalizePersonName } from "../utils/displayValue";

type Props = NativeStackScreenProps<RootStackParamList, "MessageThread">;

function formatTime(value?: string) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function MessageThreadScreen({ navigation, route }: Props) {
  const { width } = useWindowDimensions();
  const user = useAppStore((state) => state.user);
  const chatScrollRef = useRef<ScrollView | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [composerText, setComposerText] = useState("");
  const [directDetail, setDirectDetail] = useState<DirectSessionDetail | null>(null);
  const [aiMessages, setAiMessages] = useState<AIChatMessage[]>([]);
  const [aiSessionMeta, setAiSessionMeta] = useState<ChatSessionRecord | null>(null);
  const [expandedMessages, setExpandedMessages] = useState<Record<string, boolean>>({});

  const kind = route.params.kind;
  const title = route.params.title || (kind === "ai" ? AI_AGENT_CHAT_TITLE : "消息");
  const sessionId = route.params.sessionId;
  const compactLayout = width < 960;

  useEffect(() => {
    navigation.setOptions({ title });
  }, [navigation, title]);

  const load = async () => {
    shouldAutoScrollRef.current = true;
    setLoading(true);
    setError("");
    try {
      if (kind === "direct") {
        if (!user?.id || !sessionId) {
          setDirectDetail(null);
          return;
        }
        const detail = await api.getDirectSessionDetail(sessionId, user.id);
        setDirectDetail(detail);
        return;
      }

      const sessions = await loadChatSessions();
      const current =
        sessions.find((item) => item.id === sessionId || item.conversationId === sessionId) ||
        sessions[0] ||
        null;
      setAiSessionMeta(current);
      setAiMessages(current?.messages || []);
    } catch (err) {
      setError(getApiErrorMessage(err, "消息加载失败，请稍后重试。"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [kind, sessionId, user?.id]);

  const directMessages = useMemo(
    () =>
      (directDetail?.messages || []).slice().sort((a, b) => String(a.created_at).localeCompare(String(b.created_at))),
    [directDetail]
  );

  useEffect(() => {
    if (loading || !shouldAutoScrollRef.current) {
      return;
    }
    const timer = setTimeout(() => {
      chatScrollRef.current?.scrollToEnd({ animated: true });
    }, 60);
    return () => clearTimeout(timer);
  }, [aiMessages, directMessages, loading]);

  const sendMessage = async () => {
    const text = composerText.trim();
    if (!text || busy) {
      return;
    }
    shouldAutoScrollRef.current = true;
    setBusy(true);
    setError("");
    try {
      if (kind === "direct") {
        if (!user?.id || !sessionId) {
          throw new Error("missing_direct_session");
        }
        await api.sendDirectMessage({
          sessionId,
          senderId: user.id,
          content: text,
        });
        setComposerText("");
        const detail = await api.getDirectSessionDetail(sessionId, user.id);
        setDirectDetail(detail);
        return;
      }

      const nextConversationId = aiSessionMeta?.conversationId || sessionId || AI_AGENT_CHAT_SESSION_ID;
      const userMessage: AIChatMessage = {
        id: `msg-user-${Date.now()}`,
        role: "user",
        mode: "agent_cluster",
        text,
        timestamp: new Date().toISOString(),
      };
      const optimistic = [...aiMessages, userMessage];
      setAiMessages(optimistic);
      setComposerText("");

      const response = await api.runAiChat({
        mode: "agent_cluster",
        clusterProfile: "nursing_default_cluster",
        conversationId: nextConversationId,
        userInput: text,
        requestedBy: user?.id,
        executionProfile: "observe",
        operatorNotes: buildAiOperatorNotes(buildSessionMemory(aiMessages)),
      });
      const assistantMessage: AIChatMessage = {
        id: `msg-ai-${Date.now()}`,
        role: "assistant",
        mode: "agent_cluster",
        text: response.summary,
        timestamp: response.created_at,
        response,
      };
      const finalMessages = [...optimistic, assistantMessage];
      setAiMessages(finalMessages);

      const nextSession: ChatSessionRecord = {
        id: nextConversationId,
        title: aiSessionMeta?.title || AI_AGENT_CHAT_TITLE,
        conversationId: nextConversationId,
        mode: "agent_cluster",
        clusterProfile: "nursing_default_cluster",
        executionProfile: "observe",
        createdAt: aiSessionMeta?.createdAt || userMessage.timestamp,
        updatedAt: response.created_at,
        lastPrompt: text,
        lastSummary: response.summary,
        ...buildSessionMemory(finalMessages, response),
        messages: finalMessages,
      };
      const stored = upsertChatSession(await loadChatSessions(), nextSession);
      await saveChatSessions(stored);
      setAiSessionMeta(nextSession);
    } catch (err) {
      setComposerText(text);
      setError(getApiErrorMessage(err, kind === "ai" ? "智能协作回复失败。" : "消息发送失败。"));
    } finally {
      setBusy(false);
    }
  };

  const renderBubble = (payload: {
    key: string;
    mine: boolean;
    text: string;
    meta?: string;
    tags?: string[];
  }) => {
    const expandable = payload.text.length > 220 || payload.text.split("\n").length > 7;
    const expanded = compactLayout
      ? true
      : expandedMessages[payload.key] !== undefined
      ? Boolean(expandedMessages[payload.key])
      : !payload.mine;
    return (
    <View key={payload.key} style={[styles.bubbleRow, payload.mine && styles.bubbleRowMine]}>
      <View style={[styles.bubble, compactLayout && styles.bubbleCompact, payload.mine ? styles.bubbleMine : styles.bubbleOther]}>
        <Text
          style={[styles.bubbleText, payload.mine && styles.bubbleTextMine]}
          numberOfLines={!compactLayout && expandable && !expanded ? 7 : undefined}
        >
          {payload.text}
        </Text>
        {!compactLayout && expandable ? (
          <Pressable onPress={() => setExpandedMessages((current) => ({ ...current, [payload.key]: !expanded }))}>
            <Text style={[styles.expandText, payload.mine && styles.expandTextMine]}>{expanded ? "收起" : "展开全文"}</Text>
          </Pressable>
        ) : null}
        {!compactLayout && payload.tags?.length ? (
          <View style={styles.tagRow}>
            {payload.tags.map((tag) => (
              <View key={tag} style={styles.tagChip}>
                <Text style={styles.tagText}>{tag}</Text>
              </View>
            ))}
          </View>
        ) : null}
        {!compactLayout && payload.meta ? <Text style={[styles.bubbleMeta, payload.mine && styles.bubbleMetaMine]}>{payload.meta}</Text> : null}
      </View>
    </View>
  );
  };

  return (
    <SafeAreaView style={styles.page} edges={["left", "right", "bottom"]}>
      <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      {!compactLayout ? (
        <View style={styles.headerInfo}>
          <StatusPill text={kind === "ai" ? "智能协作会话" : "好友协作会话"} tone="info" />
          {kind === "ai" ? (
            <Text style={styles.headerTip}>可直接问通用护理问题，也可点名床位、多人床位或病区。</Text>
          ) : (
            <Text style={styles.headerTip}>保留像微信一样的对话方式，消息会跟随当前联系人沉淀。</Text>
          )}
        </View>
      ) : null}

      {!compactLayout && kind === "ai" && (aiSessionMeta?.memorySummary || aiSessionMeta?.memoryFacts?.length || aiSessionMeta?.memoryTodos?.length) ? (
        <View style={styles.memoryPanel}>
          <Text style={styles.memoryTitle}>会话记忆</Text>
          {aiSessionMeta?.memorySummary ? <Text style={styles.memoryText}>{aiSessionMeta.memorySummary}</Text> : null}
          {aiSessionMeta?.memoryFacts?.length ? (
            <Text style={styles.memoryMeta}>已记住：{aiSessionMeta.memoryFacts.slice(0, 3).join("；")}</Text>
          ) : null}
          {aiSessionMeta?.memoryTodos?.length ? (
            <Text style={styles.memoryMeta}>待继续：{aiSessionMeta.memoryTodos.slice(0, 3).join("；")}</Text>
          ) : null}
        </View>
      ) : null}

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.loadingText}>正在读取会话...</Text>
        </View>
      ) : (
        <ScrollView
          ref={chatScrollRef}
          style={styles.chatScroll}
          contentContainerStyle={[styles.chatContent, compactLayout && styles.chatContentCompact]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
          nestedScrollEnabled
          showsVerticalScrollIndicator
          scrollEventThrottle={16}
          onScroll={(event) => {
            const { layoutMeasurement, contentOffset, contentSize } = event.nativeEvent;
            const distanceToBottom = contentSize.height - (contentOffset.y + layoutMeasurement.height);
            shouldAutoScrollRef.current = distanceToBottom < 96;
          }}
        >
          {kind === "direct"
            ? directMessages.map((message) =>
                renderBubble({
                  key: message.id,
                  mine: message.sender_id === user?.id,
                  text: message.content,
                  meta: formatTime(message.created_at),
                })
              )
            : aiMessages.map((message) =>
                renderBubble({
                  key: message.id,
                  mine: message.role === "user",
                  text: message.role === "assistant" ? buildAssistantMessageText(message) : message.text,
                  meta: formatTime(message.timestamp),
                  tags:
                    message.role === "assistant"
                      ? [
                          message.response?.patient_name || message.response?.bed_no
                            ? `${formatBedLabel(message.response?.bed_no, "-床")} ${normalizePersonName(message.response?.patient_name)}`.trim()
                            : "通用问答",
                          compactText(buildChatSessionTitle(message.text), 20),
                        ]
                      : undefined,
                })
              )}

          {!directMessages.length && kind === "direct" ? (
            <SurfaceCard>
              <Text style={styles.emptyText}>这段好友会话还没有消息，发一条试试看。</Text>
            </SurfaceCard>
          ) : null}

          {!aiMessages.length && kind === "ai" ? (
            <SurfaceCard>
              <Text style={styles.emptyText}>智能协作已就位。你可以直接说“生成12床输血护理记录草稿”或“生成全病区交班草稿”。</Text>
            </SurfaceCard>
          ) : null}
        </ScrollView>
      )}

      <View style={styles.composerWrap}>
        <TextInput
          value={composerText}
          onChangeText={setComposerText}
          placeholder={kind === "ai" ? "输入护理问题或文书指令..." : "发消息"}
          placeholderTextColor={colors.subText}
          multiline
          style={styles.composer}
        />
        <Pressable style={[styles.sendButton, busy && styles.sendButtonDisabled]} onPress={sendMessage} disabled={busy}>
          <Text style={styles.sendText}>{busy ? "发送中..." : "发送"}</Text>
        </Pressable>
      </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: "#f5f6f7",
  },
  headerInfo: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
    gap: 8,
  },
  headerTip: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  errorText: {
    paddingHorizontal: spacing.lg,
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
  memoryPanel: {
    marginHorizontal: spacing.lg,
    marginBottom: spacing.sm,
    borderRadius: 16,
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
  loadingWrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
  loadingText: {
    color: colors.subText,
    fontSize: 13,
  },
  chatContent: {
    flexGrow: 1,
    paddingHorizontal: spacing.lg,
    paddingBottom: 120,
    gap: 12,
  },
  chatContentCompact: {
    paddingTop: spacing.sm,
    paddingBottom: 228,
  },
  chatScroll: {
    flex: 1,
  },
  bubbleRow: {
    alignItems: "flex-start",
  },
  bubbleRowMine: {
    alignItems: "flex-end",
  },
  bubble: {
    maxWidth: "90%",
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 8,
  },
  bubbleCompact: {
    maxWidth: "100%",
  },
  bubbleOther: {
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#e3e8ea",
  },
  bubbleMine: {
    backgroundColor: "#95ec69",
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
    color: "#163300",
  },
  bubbleTextMine: {
    color: "#163300",
  },
  bubbleMeta: {
    color: colors.subText,
    fontSize: 11.5,
  },
  bubbleMetaMine: {
    color: "rgba(22,51,0,0.66)",
  },
  tagRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  tagChip: {
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: "#eef5f3",
  },
  tagText: {
    color: colors.accent,
    fontSize: 11.5,
    fontWeight: "700",
  },
  composerWrap: {
    borderTopWidth: 1,
    borderTopColor: "#dde4e8",
    backgroundColor: "#ffffff",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: 32,
    gap: 10,
  },
  composer: {
    minHeight: 72,
    maxHeight: 140,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#f7f9fa",
    color: colors.text,
    paddingHorizontal: 14,
    paddingVertical: 12,
    textAlignVertical: "top",
    lineHeight: 20,
  },
  sendButton: {
    alignSelf: "flex-end",
    minWidth: 96,
    borderRadius: 999,
    paddingHorizontal: 18,
    paddingVertical: 12,
    backgroundColor: "#07c160",
  },
  sendButtonDisabled: {
    opacity: 0.6,
  },
  sendText: {
    color: "#ffffff",
    fontSize: 13.5,
    fontWeight: "800",
    textAlign: "center",
  },
  emptyText: {
    color: colors.subText,
    fontSize: 13,
    lineHeight: 20,
  },
});
