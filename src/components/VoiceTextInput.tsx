import React, { useEffect, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TextInput, View } from "react-native";
import { Audio } from "expo-av";
import * as FileSystem from "expo-file-system";

import { api } from "../api/endpoints";
import { ActionButton, StatusPill, SurfaceCard } from "./ui";
import { colors, radius, spacing } from "../theme";

type Props = {
  value: string;
  onChangeText: (text: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  embedded?: boolean;
};

function isInvalidTranscription(text: string, provider: string): boolean {
  if (!text) {
    return true;
  }
  const blockedPhrases = [
    "语音转写失败",
    "手动输入",
    "语音已接收",
    "请补充床号",
    "未识别到有效语音",
  ];
  if (provider === "fallback") {
    return true;
  }
  return blockedPhrases.some((item) => text.includes(item));
}

const uriToBase64 = async (uri: string) => {
  try {
    return await FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 });
  } catch {
    const response = await fetch(uri);
    const blob = await response.blob();
    const base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const result = String(reader.result || "");
        const parts = result.split(",");
        resolve(parts.length > 1 ? parts[1] : "");
      };
      reader.onerror = () => reject(new Error("read_file_failed"));
      reader.readAsDataURL(blob);
    });
    return base64;
  }
};

export function VoiceTextInput({ value, onChangeText, onSubmit, placeholder, embedded = false }: Props) {
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [busy, setBusy] = useState(false);
  const [recordingOn, setRecordingOn] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [voiceStatus, setVoiceStatus] = useState("");

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
        Alert.alert("麦克风权限被拒绝");
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
      setVoiceStatus("录音已开始，请说话。");
    } catch {
      Alert.alert("录音启动失败");
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

      let audioBase64: string | undefined;
      if (uri) {
        audioBase64 = await uriToBase64(uri);
      }

      const result = await api.transcribe({
        audioBase64,
        textHint: value || undefined,
      });

      const provider = String(result?.provider || "asr");
      const nextText = String(result?.text || "").trim();
      if (isInvalidTranscription(nextText, provider)) {
        setVoiceStatus("当前未返回有效语音文本，请重试或继续手动输入。");
        Alert.alert("语音识别未成功", "请靠近麦克风重试，或继续手动输入。");
        return;
      }

      onChangeText(value ? `${value} ${nextText}` : nextText);
      setVoiceStatus(`转写完成：${provider}`);
    } catch {
      setVoiceStatus("语音转写失败，请重试或手动输入。");
      Alert.alert("语音转写失败", "可继续手动输入。");
    } finally {
      setRecording(null);
      setRecordingOn(false);
      setBusy(false);
      setRecordSeconds(0);
    }
  };

  const content = (
    <>
      <TextInput
        style={styles.input}
        multiline
        placeholder={placeholder || "请输入"}
        placeholderTextColor={colors.subText}
        value={value}
        onChangeText={onChangeText}
      />
      <Text style={styles.tipLine}>支持语音和打字混合输入，系统会自动合并。</Text>
      <View style={styles.row}>
        <ActionButton
          label={recordingOn ? "停止录音并转写" : "语音录入"}
          onPress={recordingOn ? stopRecordingAndTranscribe : startRecording}
          variant={recordingOn ? "danger" : "secondary"}
          style={styles.actionBtn}
        />
        <ActionButton label="发送" onPress={onSubmit} style={styles.actionBtn} />
      </View>
      {busy ? (
        <View style={styles.busyRow}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.busyText}>正在处理语音...</Text>
        </View>
      ) : null}
      {recordingOn ? <StatusPill text={`录音中 ${recordSeconds}s`} tone="warning" /> : null}
      {voiceStatus ? <Text style={styles.busyText}>{voiceStatus}</Text> : null}
    </>
  );

  if (embedded) {
    return <View style={styles.embeddedShell}>{content}</View>;
  }

  return (
    <SurfaceCard style={styles.box}>
      {content}
    </SurfaceCard>
  );
}

const styles = StyleSheet.create({
  embeddedShell: {
    gap: spacing.sm,
  },
  box: {
    gap: spacing.sm,
  },
  input: {
    minHeight: 100,
    textAlignVertical: "top",
    color: colors.text,
    fontSize: 15.5,
    lineHeight: 23,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: "#fbfdff",
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  tipLine: { color: colors.subText, fontSize: 12.5 },
  row: {
    flexDirection: "row",
    gap: 10,
  },
  actionBtn: { flex: 1 },
  busyRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  busyText: {
    color: colors.subText,
    fontSize: 13,
  },
});
