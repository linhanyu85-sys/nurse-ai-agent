import React, { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { api, getApiErrorMessage } from "../api/endpoints";
import { ActionButton, ScreenShell, StatusPill, SurfaceCard } from "../components/ui";
import { useAppStore } from "../store/appStore";
import { colors } from "../theme";
import type { AIRuntimeStatus } from "../types";
import { getDepartmentLabel, getEngineLabel, getModelLabel, getRoleLabel } from "../utils/displayText";
import { normalizePersonName } from "../utils/displayValue";

export function ProfileScreen() {
  const user = useAppStore((state) => state.user);
  const departmentId = useAppStore((state) => state.selectedDepartmentId);
  const logout = useAppStore((state) => state.logout);
  const [runtime, setRuntime] = useState<AIRuntimeStatus | null>(null);
  const [error, setError] = useState("");

  const loadRuntime = async () => {
    setError("");
    try {
      const data = await api.getAiRuntimeStatus();
      setRuntime(data);
    } catch (err) {
      setError(getApiErrorMessage(err, "运行状态读取失败，请稍后重试。"));
    }
  };

  useEffect(() => {
    loadRuntime();
  }, []);

  return (
    <ScreenShell
      title={normalizePersonName(user?.full_name, "我的")}
      subtitle="核心配置尽量收敛，避免把临床工作台做成设置中心"
      rightNode={<StatusPill text={getRoleLabel(user?.role_code || "nurse")} tone="info" />}
    >
      <SurfaceCard>
        <Text style={styles.sectionTitle}>账号信息</Text>
        <Text style={styles.metaText}>用户 ID：{user?.id || "-"}</Text>
        <Text style={styles.metaText}>当前病区：{getDepartmentLabel(departmentId)}</Text>
      </SurfaceCard>

      <SurfaceCard>
        <View style={styles.headerRow}>
          <Text style={styles.sectionTitle}>运行状态</Text>
          <ActionButton label="刷新" onPress={loadRuntime} variant="secondary" style={styles.actionButton} />
        </View>
        {runtime ? (
          <>
            <Text style={styles.metaText}>当前引擎：{getEngineLabel(runtime.active_engine)}</Text>
            <Text style={styles.metaText}>本地模型服务：{runtime.local_model_service_reachable ? "可用" : "不可用"}</Text>
            <Text style={styles.metaText}>
              可用模型：{runtime.available_local_models.length ? runtime.available_local_models.map((item) => getModelLabel(item)).join("、") : "暂无"}
            </Text>
            {runtime.local_model_aliases?.primary ? (
              <Text style={styles.metaText}>主模型：{getModelLabel(runtime.local_model_aliases.primary)}</Text>
            ) : null}
            {runtime.local_model_aliases?.fallback ? (
              <Text style={styles.metaText}>兜底模型：{getModelLabel(runtime.local_model_aliases.fallback)}</Text>
            ) : null}
            {runtime.local_model_aliases?.planner ? (
              <Text style={styles.metaText}>规划模型：{getModelLabel(runtime.local_model_aliases.planner)}</Text>
            ) : null}
            {runtime.local_model_aliases?.tcm ? (
              <Text style={styles.metaText}>中医模型：{getModelLabel(runtime.local_model_aliases.tcm)}</Text>
            ) : null}
            {runtime.task_queue ? (
              <Text style={styles.metaText}>
                队列：排队 {runtime.task_queue.queued} · 运行 {runtime.task_queue.running} · 待批准 {runtime.task_queue.waiting_approval}
              </Text>
            ) : null}
            {runtime.fallback_reason ? <Text style={styles.metaText}>回退原因：{runtime.fallback_reason}</Text> : null}
          </>
        ) : (
          <Text style={styles.metaText}>正在读取运行状态...</Text>
        )}
        {error ? <Text style={styles.errorText}>{error}</Text> : null}
      </SurfaceCard>

      {runtime && !runtime.local_model_service_reachable ? (
        <SurfaceCard>
          <Text style={styles.sectionTitle}>连接建议</Text>
          <Text style={styles.metaText}>1. 先确认本地模型服务已经启动，并且手机与电脑在同一局域网。</Text>
          <Text style={styles.metaText}>2. 若工作台频繁超时，优先使用“快速观察”策略，等模型恢复后再跑长链路任务。</Text>
          <Text style={styles.metaText}>3. 若仍提示连接异常，检查 API 地址和模型别名是否与当前部署一致。</Text>
        </SurfaceCard>
      ) : null}

      <SurfaceCard>
        <Text style={styles.sectionTitle}>产品原则</Text>
        <Text style={styles.metaText}>1. 病例只在病区页集中展示。</Text>
        <Text style={styles.metaText}>2. 智能工作台默认依靠自然语言定位床位。</Text>
        <Text style={styles.metaText}>3. 中医问诊仅作护理辅助，不替代医师诊疗。</Text>
      </SurfaceCard>

      <ActionButton label="退出登录" onPress={logout} variant="danger" />
    </ScreenShell>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
  },
  actionButton: {
    minWidth: 76,
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
  },
  metaText: {
    color: colors.subText,
    fontSize: 13,
    lineHeight: 20,
  },
  errorText: {
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
});
