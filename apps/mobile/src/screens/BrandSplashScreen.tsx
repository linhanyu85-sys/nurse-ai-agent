import React from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors } from "../theme";

export function BrandSplashScreen() {
  return (
    <SafeAreaView style={styles.safe} edges={["top", "left", "right", "bottom"]}>
      <View style={styles.bgGlowTop} />
      <View style={styles.bgGlowBottom} />
      <View style={styles.content}>
        <Image source={require("../../assets/brand-logo.png")} style={styles.logo} resizeMode="contain" />
        <View style={styles.badge}>
          <Text style={styles.badgeText}>临床智护</Text>
        </View>
        <Text style={styles.title}>护理智能协作</Text>
        <Text style={styles.subTitle}>面向临床护理协作与文书处理的智能工作台</Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: "#eef4f5",
  },
  bgGlowTop: {
    position: "absolute",
    top: -40,
    right: -50,
    width: 220,
    height: 220,
    borderRadius: 110,
    backgroundColor: "rgba(15, 92, 125, 0.14)",
  },
  bgGlowBottom: {
    position: "absolute",
    left: -60,
    bottom: -40,
    width: 240,
    height: 240,
    borderRadius: 120,
    backgroundColor: "rgba(24, 141, 118, 0.12)",
  },
  content: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 28,
    gap: 12,
  },
  logo: {
    width: 148,
    height: 148,
    borderRadius: 32,
  },
  badge: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: "#dfecef",
  },
  badgeText: {
    color: "#2b5b67",
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.4,
  },
  title: {
    color: colors.primary,
    fontSize: 34,
    fontWeight: "800",
  },
  subTitle: {
    color: colors.subText,
    fontSize: 15,
    lineHeight: 22,
    textAlign: "center",
  },
});
