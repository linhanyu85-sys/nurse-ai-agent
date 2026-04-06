import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import { isMockMode } from "../api/client";
import { api, getApiErrorMessage } from "../api/endpoints";
import { subscribeWardBeds } from "../api/realtime";
import { ActionButton, InfoBanner, ScreenShell, StatusPill, SurfaceCard } from "../components/ui";
import type { RootStackParamList } from "../navigation/RootNavigator";
import { useAppStore } from "../store/appStore";
import { colors, spacing } from "../theme";
import type { BedOverview } from "../types";
import { buildClinicalRiskBadge } from "../utils/clinicalRisk";
import { formatBedLabel, normalizePersonName } from "../utils/displayValue";
import { buildNursingLevelTone } from "../utils/nursingLevel";

const HEATMAP_LEVELS = ["危急", "高危", "中危", "低危", "待核对"] as const;

function riskToneStyle(level: string) {
  if (level === "危急") {
    return {
      backgroundColor: "#fff0f0",
      borderColor: "#f3b2b2",
      labelColor: "#a61d24",
    };
  }
  if (level === "高危") {
    return {
      backgroundColor: "#fff5eb",
      borderColor: "#f4c58e",
      labelColor: "#a84a00",
    };
  }
  if (level === "中危") {
    return {
      backgroundColor: "#fffbe7",
      borderColor: "#ead28f",
      labelColor: "#7c5b00",
    };
  }
  if (level === "低危") {
    return {
      backgroundColor: "#eef9f2",
      borderColor: "#b7ddc4",
      labelColor: "#17653a",
    };
  }
  return {
    backgroundColor: "#eef6ff",
    borderColor: "#c8d9ee",
    labelColor: "#2556a8",
  };
}

function formatSyncLabel(value?: string) {
  if (!value) {
    return "-";
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

export function WardOverviewScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const departmentId = useAppStore((state) => state.selectedDepartmentId);
  const setSelectedPatient = useAppStore((state) => state.setSelectedPatient);
  const [loading, setLoading] = useState(true);
  const [beds, setBeds] = useState<BedOverview[]>([]);
  const [keyword, setKeyword] = useState("");
  const [streamStatus, setStreamStatus] = useState("未连接");
  const [lastSuccessAt, setLastSuccessAt] = useState("");
  const [error, setError] = useState("");

  const loadBeds = async () => {
    setLoading(true);
    try {
      const data = await api.getWardBeds(departmentId);
      setBeds(Array.isArray(data) ? data : []);
      setLastSuccessAt(new Date().toISOString());
      setError("");
    } catch (err) {
      setBeds([]);
      setError(getApiErrorMessage(err, "病区床位加载失败，当前不展示任何推断性热力图。"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBeds();
  }, [departmentId]);

  useEffect(() => {
    const unsubscribe = subscribeWardBeds(
      departmentId,
      (payload) => {
        if (payload?.type === "ward_beds_update" && Array.isArray(payload?.data)) {
          setBeds(payload.data);
          setStreamStatus("在线");
          setLastSuccessAt(new Date().toISOString());
        } else if (payload?.type === "heartbeat") {
          setStreamStatus("在线");
          setLastSuccessAt(new Date().toISOString());
        }
      },
      () => setStreamStatus("连接异常")
    );
    return unsubscribe;
  }, [departmentId]);

  const admittedBeds = useMemo(() => beds.filter((item) => item.current_patient_id), [beds]);

  const filteredBeds = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return admittedBeds.filter((bed) => {
      const text = [
        bed.bed_no,
        bed.room_no || "",
        bed.patient_name || "",
        bed.risk_level || "",
        bed.risk_reason || "",
        ...(bed.risk_tags || []),
        ...(bed.pending_tasks || []),
        bed.latest_document_sync || "",
      ]
        .join(" ")
        .toLowerCase();
      return text.includes(needle);
    });
  }, [admittedBeds, keyword]);

  const bedRows = useMemo(
    () =>
      filteredBeds.map((item) => ({
        ...item,
        badge: buildClinicalRiskBadge(item),
      })),
    [filteredBeds]
  );

  const verifiedHeatmapBeds = useMemo(
    () => [...bedRows].filter((item) => item.badge.canUseHeatmap).sort((a, b) => b.badge.sortKey - a.badge.sortKey),
    [bedRows]
  );

  const pendingReviewBeds = useMemo(
    () => bedRows.filter((item) => !item.badge.canUseHeatmap),
    [bedRows]
  );

  const riskCounts = useMemo(() => {
    const counts: Record<(typeof HEATMAP_LEVELS)[number], number> = {
      危急: 0,
      高危: 0,
      中危: 0,
      低危: 0,
      待核对: 0,
    };

    bedRows.forEach((bed) => {
      if (bed.badge.canUseHeatmap && (bed.badge.label as (typeof HEATMAP_LEVELS)[number]) in counts) {
        counts[bed.badge.label as keyof typeof counts] += 1;
      } else {
        counts.待核对 += 1;
      }
    });
    return counts;
  }, [bedRows]);

  const topBeds = useMemo(() => verifiedHeatmapBeds.slice(0, 8), [verifiedHeatmapBeds]);

  const openPatientArchive = async (bed: BedOverview) => {
    if (!bed.current_patient_id) {
      return;
    }
    try {
      const patient = await api.getPatient(bed.current_patient_id);
      setSelectedPatient(patient);
      navigation.navigate("PatientDetail", { patientId: bed.current_patient_id });
    } catch (err) {
      setError(getApiErrorMessage(err, "患者档案读取失败，请稍后重试。"));
    }
  };

  return (
    <ScreenShell
      title="病区总览"
      subtitle="只展示真实病区数据；缺少可靠分层结果的床位不会进入热力图。"
      rightNode={<StatusPill text={streamStatus} tone={streamStatus === "连接异常" ? "danger" : "success"} />}
    >
      {isMockMode ? (
        <InfoBanner
          title="当前为演示模式"
          description="页面正在使用 mock 数据，请勿用于真实临床判断或交接班。"
          tone="danger"
        />
      ) : (
        <InfoBanner
          title="当前展示真实病区接口数据"
          description={`最近成功刷新：${formatSyncLabel(lastSuccessAt)}；没有结构化风险分层的床位会标记为“待核对”，不会直接着色。`}
          tone="success"
        />
      )}

      {pendingReviewBeds.length ? (
        <InfoBanner
          title={`${pendingReviewBeds.length} 床待人工核对`}
          description="这些床位有风险标签或待办，但未收到结构化风险分层，已从热力图排除，避免把推断结果当成真实风险等级。"
          tone="warning"
        />
      ) : null}

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <SurfaceCard>
        <View style={styles.headerRow}>
          <View style={styles.searchWrap}>
            <TextInput
              value={keyword}
              onChangeText={setKeyword}
              placeholder="搜索床号、姓名、风险等级或待办"
              placeholderTextColor={colors.subText}
              style={styles.searchInput}
            />
          </View>
          <ActionButton label="刷新" onPress={loadBeds} variant="secondary" style={styles.refreshButton} />
        </View>
        <Text style={styles.metaText}>
          当前病区 {admittedBeds.length} 位在床患者 · 已核实 {verifiedHeatmapBeds.length} 位 · 待核对 {pendingReviewBeds.length} 位 · 最近更新{" "}
          {formatSyncLabel(lastSuccessAt)}
        </Text>
      </SurfaceCard>

      <SurfaceCard>
        <Text style={styles.sectionTitle}>病区风险热力图</Text>
        <Text style={styles.sectionMeta}>先看已核实风险等级，再点患者进入病例档案；未核对床位不着色，避免误导。</Text>
        <View style={styles.summaryRow}>
          {HEATMAP_LEVELS.map((level) => {
            const tone = riskToneStyle(level);
            return (
              <View key={level} style={[styles.summaryBox, { backgroundColor: tone.backgroundColor, borderColor: tone.borderColor }]}>
                <Text style={[styles.summaryBoxLabel, { color: tone.labelColor }]}>{level}</Text>
                <Text style={styles.summaryBoxCount}>{riskCounts[level]}</Text>
              </View>
            );
          })}
        </View>

        <View style={styles.heatGrid}>
          {topBeds.map((bed) => {
            const tone = riskToneStyle(bed.badge.label);
            const nursingTone = buildNursingLevelTone(bed);
            return (
              <Pressable
                key={bed.id}
                style={[styles.heatCell, { backgroundColor: tone.backgroundColor, borderColor: tone.borderColor }]}
                onPress={() => openPatientArchive(bed)}
              >
                <View style={styles.heatHead}>
                  <View
                    style={[
                      styles.bedColorPlate,
                      {
                        backgroundColor: nursingTone.backgroundColor,
                        borderColor: nursingTone.borderColor,
                      },
                    ]}
                  >
                    <Text style={[styles.bedColorPlateText, { color: nursingTone.textColor }]}>{formatBedLabel(bed.bed_no)}</Text>
                  </View>
                </View>
                <Text style={styles.heatCellName} numberOfLines={1}>
                  {normalizePersonName(bed.patient_name, "待确认")}
                </Text>
                <Text style={[styles.nursingLevelInline, { color: nursingTone.textColor }]}>{nursingTone.label}</Text>
                <Text style={[styles.heatCellRisk, { color: tone.labelColor }]}>{bed.badge.label}</Text>
                <Text style={styles.heatCellMeta} numberOfLines={2}>
                  {bed.badge.shortReason}
                </Text>
              </Pressable>
            );
          })}
          {!topBeds.length ? (
            <Text style={styles.loadingText}>
              当前没有可安全展示的结构化热力图，请先确认病区接口或补齐风险分层字段。
            </Text>
          ) : null}
        </View>
      </SurfaceCard>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.loadingText}>正在读取病区患者…</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.listContent}>
          <SurfaceCard>
            <Text style={styles.sectionTitle}>重点患者列表</Text>
            <Text style={styles.sectionMeta}>每张卡片都会明确区分已核实风险和待核对线索，避免把线索当成结论。</Text>
          </SurfaceCard>

          {bedRows.map((bed) => {
            const risk = bed.badge;
            const nursingTone = buildNursingLevelTone(bed);
            return (
              <Pressable key={bed.id} style={styles.pressable} onPress={() => openPatientArchive(bed)}>
                <SurfaceCard style={styles.bedCard}>
                  <View style={styles.cardTop}>
                    <View style={styles.bedHead}>
                      <View
                        style={[
                          styles.bedColorPlate,
                          {
                            backgroundColor: nursingTone.backgroundColor,
                            borderColor: nursingTone.borderColor,
                          },
                        ]}
                      >
                        <Text style={[styles.bedColorPlateText, { color: nursingTone.textColor }]}>{formatBedLabel(bed.bed_no)}</Text>
                      </View>
                      <Text style={styles.roomNo}>房间 {bed.room_no || "-"}</Text>
                    </View>
                    <View style={styles.cardBadges}>
                      <View
                        style={[
                          styles.nursingLevelPill,
                          {
                            backgroundColor: nursingTone.backgroundColor,
                            borderColor: nursingTone.borderColor,
                          },
                        ]}
                      >
                        <Text style={[styles.nursingLevelPillText, { color: nursingTone.textColor }]}>{nursingTone.label}</Text>
                      </View>
                      <StatusPill text={risk.label} tone={risk.tone} />
                    </View>
                  </View>

                  <View style={styles.nameRow}>
                    <Text style={styles.patientName}>{normalizePersonName(bed.patient_name, "未识别姓名")}</Text>
                    <Text style={styles.archiveHint}>进入档案</Text>
                  </View>

                  <Text style={risk.source === "structured" ? styles.reasonText : styles.warningText}>
                    {risk.source === "structured" ? `分层依据：${risk.shortReason}` : risk.warning}
                  </Text>
                  <Text style={styles.summaryText}>
                    风险标签：{bed.risk_tags.slice(0, 3).join("、") || "暂无"} · 待办：{bed.pending_tasks.slice(0, 3).join("、") || "暂无"}
                  </Text>
                  {bed.latest_document_sync ? <Text style={styles.docText}>{bed.latest_document_sync}</Text> : null}
                </SurfaceCard>
              </Pressable>
            );
          })}

          {!bedRows.length ? (
            <SurfaceCard>
              <Text style={styles.loadingText}>没有找到匹配患者，请换一个关键词再试。</Text>
            </SurfaceCard>
          ) : null}
        </ScrollView>
      )}
    </ScreenShell>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    gap: 10,
    alignItems: "center",
  },
  searchWrap: {
    flex: 1,
  },
  searchInput: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d7e0e2",
    backgroundColor: "#ffffff",
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 11,
  },
  refreshButton: {
    minWidth: 80,
  },
  errorText: {
    color: colors.danger,
    fontSize: 12.5,
    fontWeight: "700",
  },
  metaText: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
  },
  sectionTitle: {
    color: colors.primary,
    fontSize: 15,
    fontWeight: "800",
  },
  sectionMeta: {
    color: colors.subText,
    fontSize: 12.5,
    lineHeight: 18,
    marginTop: 4,
  },
  summaryRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12,
  },
  summaryBox: {
    minWidth: "30%",
    flexGrow: 1,
    borderRadius: 14,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 10,
    gap: 4,
  },
  summaryBoxLabel: {
    fontSize: 12.5,
    fontWeight: "800",
  },
  summaryBoxCount: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "800",
  },
  heatGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 12,
  },
  heatCell: {
    width: "48%",
    borderRadius: 16,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 12,
    gap: 4,
  },
  heatHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  bedColorPlate: {
    minWidth: 62,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 5,
    alignItems: "center",
    justifyContent: "center",
  },
  bedColorPlateText: {
    fontSize: 15,
    fontWeight: "800",
  },
  heatCellBed: {
    fontSize: 16,
    fontWeight: "800",
  },
  heatCellName: {
    color: colors.text,
    fontSize: 13.5,
    fontWeight: "700",
  },
  heatCellRisk: {
    fontSize: 12.5,
    fontWeight: "700",
  },
  nursingLevelInline: {
    fontSize: 12.5,
    fontWeight: "700",
  },
  heatCellMeta: {
    color: colors.subText,
    fontSize: 12,
    lineHeight: 17,
  },
  loadingWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  loadingText: {
    color: colors.subText,
    fontSize: 13,
    lineHeight: 19,
  },
  listContent: {
    gap: 12,
  },
  pressable: {
    borderRadius: 18,
  },
  bedCard: {
    gap: 10,
  },
  cardTop: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 12,
  },
  bedHead: {
    gap: 2,
  },
  cardBadges: {
    alignItems: "flex-end",
    gap: 6,
  },
  nursingLevelPill: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  nursingLevelPillText: {
    fontSize: 12,
    fontWeight: "800",
  },
  bedNo: {
    color: colors.primary,
    fontSize: 20,
    fontWeight: "800",
  },
  roomNo: {
    color: colors.subText,
    fontSize: 12.5,
  },
  nameRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  patientName: {
    flex: 1,
    color: colors.text,
    fontSize: 16,
    fontWeight: "700",
  },
  archiveHint: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "800",
  },
  reasonText: {
    color: colors.warning,
    fontSize: 12.5,
    fontWeight: "700",
    lineHeight: 18,
  },
  warningText: {
    color: "#a66300",
    fontSize: 12.5,
    fontWeight: "700",
    lineHeight: 18,
  },
  summaryText: {
    color: colors.subText,
    fontSize: 13,
    lineHeight: 20,
  },
  docText: {
    color: colors.primary,
    fontSize: 12.5,
    fontWeight: "700",
  },
});
