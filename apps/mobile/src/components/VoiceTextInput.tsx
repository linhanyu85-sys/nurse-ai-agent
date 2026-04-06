import React, { useEffect, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TextInput, View } from "react-native";
import { Audio } from "expo-av";
import * as FileSystem from "expo-file-system";

import { api } from "../api/endpoints";
import { ActionButton, StatusPill } from "./ui";
import { colors, radius, spacing } from "../theme";

type Props = {
  value: string;
  onChangeText: (text: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  compact?: boolean;
};

function isInvalidTranscription(text: string, provider: string): boolean {
  if (!text) {
    return true;
  }
  if (provider === "fallback") {
    return true;
  }
  const blockedPhrases = ["语音转写失败", "手动输入", "请补充床号", "未识别到有效语音"];
  return blockedPhrases.some((item) => text.includes(item));
}

async function uriToBase64(uri: string) {
  try {
    return await FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 });
  } catch {
    const response = await fetch(uri);
    const blob = await response.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const result = String(reader.result || "");
        const parts = result.split(",");
        resolve(parts.length > 1 ? parts[1] : "");
      };
      reader.onerror = () => reject(new Error("read_file_failed"));
      reader.readAsDataURL(blob);
    });
  }
}

export function VoiceTextInput({ value, onChangeText, onSubmit, placeholder, compact = false }: Props) {
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [recordingOn, setRecordingOn] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("");

  useEffect(() => {
    return () => {
      if (recording) {
        recording.stopAndUnloadAsync().catch(() => null);
      }
    };
  }, [recording]);

  useEffect(() => {
    if (!recordingOn) {
      return;
    }
    const timer = setInterval(() => setRecordSeconds((prev) => prev + 1), 1000);
    return () => clearInterval(timer);
  }, [recordingOn]);

  const startRecording = async () => {
    try {
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("未获得麦克风权限", "请先允许应用使用麦克风。");
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
        shouldDuckAndroid: true,
        playThroughEarpieceAndroid: false,
        staysActiveInBackground: false,
      });

      const { recording: nextRecording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );
      setRecording(nextRecording);
      setRecordingOn(true);
      setRecordSeconds(0);
      setStatusText("正在录音，请直接说床号、病区或问题。");
    } catch {
      Alert.alert("录音启动失败", "请稍后重试。");
    }
  };

  const stopRecordingAndTranscribe = async () => {
    if (!recording) {
      return;
    }

    setBusy(true);
    try {
      await recording.stopAndUnloadAsync();
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: false,
        playsInSilentModeIOS: true,
      });
      const uri = recording.getURI();
      const audioBase64 = uri ? await uriToBase64(uri) : undefined;
      const result = await api.transcribe({
        audioBase64,
        textHint: value || undefined,
      });

      const provider = String(result?.provider || "asr");
      const text = String(result?.text || "").trim();
      if (isInvalidTranscription(text, provider)) {
        setStatusText("没有识别到有效语音，你也可以直接输入。");
        Alert.alert("语音识别失败", "请靠近麦克风重试，或直接打字输入。");
        return;
      }

      onChangeText(value ? `${value} ${text}` : text);
      setStatusText(`已完成语音转写 · ${provider}`);
    } catch {
      setStatusText("语音转写失败，你也可以继续直接输入。");
      Alert.alert("语音转写失败", "请稍后重试，或直接打字输入。");
    } finally {
      setRecording(null);
      setRecordingOn(false);
      setBusy(false);
      setRecordSeconds(0);
    }
  };

  return (
    <View style={styles.wrap}>
      <TextInput
        style={[styles.input, compact && styles.inputCompact]}
        multiline
        scrollEnabled
        placeholder={placeholder || "直接输入问题"}
        placeholderTextColor={colors.subText}
        value={value}
        onChangeText={onChangeText}
      />

      {!compact ? (
        <Text style={styles.tip}>支持语音和键盘混合输入，系统会自动识别床号、多病例和病区范围。</Text>
      ) : null}

      <View style={styles.actionRow}>
        <ActionButton
          label={recordingOn ? "停止录音" : "语音输入"}
          onPress={recordingOn ? stopRecordingAndTranscribe : startRecording}
          variant={recordingOn ? "danger" : "secondary"}
          style={styles.actionButton}
          disabled={busy}
        />
        <ActionButton label="发送" onPress={onSubmit} style={styles.actionButton} disabled={busy} />
      </View>

      {busy ? (
        <View style={styles.busyRow}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.busyText}>正在处理语音...</Text>
        </View>
      ) : null}

      {recordingOn ? <StatusPill text={`录音中 ${recordSeconds}s`} tone="warning" /> : null}
      {statusText ? <Text style={styles.statusText}>{statusText}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: spacing.sm,
  },
  input: {
    minHeight: 110,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 15,
    lineHeight: 22,
    textAlignVertical: "top",
  },
  inputCompact: {
    minHeight: 56,
    maxHeight: 112,
    borderRadius: 16,
    backgroundColor: "#f8fafb",
  },
  tip: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  actionRow: {
    flexDirection: "row",
    gap: 10,
  },
  actionButton: {
    flex: 1,
  },
  busyRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  busyText: {
    color: colors.subText,
    fontSize: 12.5,
  },
  statusText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
});
