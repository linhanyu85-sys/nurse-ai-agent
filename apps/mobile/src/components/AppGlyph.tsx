import React from "react";
import { StyleSheet, View } from "react-native";

type GlyphName =
  | "menu"
  | "close"
  | "refresh"
  | "workspace"
  | "ward"
  | "message"
  | "profile"
  | "inbox"
  | "contacts"
  | "history"
  | "document"
  | "chevron";

function Stroke({
  color,
  width,
  rotate = "0deg",
  style,
}: {
  color: string;
  width: number | string;
  rotate?: string;
  style?: object;
}) {
  return <View style={[styles.stroke, { backgroundColor: color, width, transform: [{ rotate }] }, style]} />;
}

function MenuIcon({ color }: { color: string }) {
  return (
    <View style={styles.menuWrap}>
      <Stroke color={color} width={16} />
      <Stroke color={color} width={16} style={styles.menuGap} />
      <Stroke color={color} width={16} style={styles.menuGap} />
    </View>
  );
}

function CloseIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <Stroke color={color} width={16} rotate="45deg" style={styles.closeLine} />
      <Stroke color={color} width={16} rotate="-45deg" style={styles.closeLine} />
    </View>
  );
}

function RefreshIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.refreshArc, { borderColor: color, borderBottomColor: "transparent" }]} />
      <View style={[styles.refreshHead, { borderTopColor: color, borderRightColor: color }]} />
    </View>
  );
}

function WorkspaceIcon({ color, active }: { color: string; active: boolean }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.workspaceShell, { borderColor: color, backgroundColor: active ? `${color}12` : "transparent" }]}>
        <View style={[styles.workspaceHead, { backgroundColor: color }]} />
        <View style={styles.workspacePulseWrap}>
          <Stroke color={color} width={3} style={styles.workspacePulseLow} />
          <Stroke color={color} width={4} rotate="-38deg" style={styles.workspacePulseRise} />
          <Stroke color={color} width={5} rotate="42deg" style={styles.workspacePulseFall} />
          <Stroke color={color} width={4} style={styles.workspacePulseTail} />
        </View>
      </View>
    </View>
  );
}

function WardIcon({ color, active }: { color: string; active: boolean }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.bedFrame, { borderColor: color, backgroundColor: active ? `${color}10` : "transparent" }]}>
        <View style={[styles.bedHead, { backgroundColor: color }]} />
        <View style={[styles.bedBody, { borderColor: color }]} />
        <View style={[styles.bedLeg, { backgroundColor: color, left: 2 }]} />
        <View style={[styles.bedLeg, { backgroundColor: color, right: 2 }]} />
        <View style={[styles.wardCrossV, { backgroundColor: color }]} />
        <View style={[styles.wardCrossH, { backgroundColor: color }]} />
      </View>
    </View>
  );
}

function MessageIcon({ color, active }: { color: string; active: boolean }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.clipboardShell, { borderColor: color, backgroundColor: active ? `${color}10` : "transparent" }]}>
        <View style={[styles.clipboardClip, { backgroundColor: color }]} />
        <View style={[styles.clipboardCrossV, { backgroundColor: color }]} />
        <View style={[styles.clipboardCrossH, { backgroundColor: color }]} />
        <Stroke color={color} width={9} style={styles.clipboardLine} />
        <Stroke color={color} width={9} style={styles.clipboardLineGap} />
        <Stroke color={color} width={6} style={styles.clipboardLineGap} />
      </View>
    </View>
  );
}

function ProfileIcon({ color, active }: { color: string; active: boolean }) {
  return (
    <View style={styles.centerWrap}>
      <View style={styles.centerWrap}>
        <View style={[styles.nurseCap, { borderColor: color, backgroundColor: active ? `${color}10` : "transparent" }]}>
          <View style={[styles.nurseCapWing, { borderColor: color, left: 0 }]} />
          <View style={[styles.nurseCapWing, { borderColor: color, right: 0, transform: [{ rotate: "180deg" }] }]} />
          <View style={[styles.nurseCapCrossV, { backgroundColor: color }]} />
          <View style={[styles.nurseCapCrossH, { backgroundColor: color }]} />
        </View>
        <View style={[styles.badgeHead, { borderColor: color, top: 7 }]} />
        <View style={[styles.profileBody, { borderColor: color, top: 11 }]} />
      </View>
    </View>
  );
}

function InboxIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.inboxBox, { borderColor: color }]}>
        <View style={[styles.inboxNotch, { borderColor: color }]} />
      </View>
    </View>
  );
}

function ContactsIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.contactHeadSmall, { borderColor: color, left: 2 }]} />
      <View style={[styles.contactHeadSmall, { borderColor: color, right: 2 }]} />
      <View style={[styles.contactBody, { borderColor: color }]} />
    </View>
  );
}

function HistoryIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.clockFace, { borderColor: color }]}>
        <Stroke color={color} width={1.5} rotate="0deg" style={styles.clockHandLong} />
        <Stroke color={color} width={1.5} rotate="55deg" style={styles.clockHandShort} />
      </View>
    </View>
  );
}

function DocumentIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <View style={[styles.documentShell, { borderColor: color }]}>
        <View style={[styles.documentCorner, { borderColor: color }]} />
        <Stroke color={color} width={9} style={styles.documentLine} />
        <Stroke color={color} width={9} style={styles.documentLineGap} />
      </View>
    </View>
  );
}

function ChevronIcon({ color }: { color: string }) {
  return (
    <View style={styles.centerWrap}>
      <Stroke color={color} width={8} rotate="45deg" style={styles.chevronLeft} />
      <Stroke color={color} width={8} rotate="-45deg" style={styles.chevronRight} />
    </View>
  );
}

export function AppGlyph({
  name,
  size = 18,
  color = "#1e293b",
  active = false,
}: {
  name: GlyphName;
  size?: number;
  color?: string;
  active?: boolean;
}) {
  const scale = Math.max(size / 18, 0.8);

  return (
    <View style={[styles.wrap, { transform: [{ scale }] }]}>
      {name === "menu" && <MenuIcon color={color} />}
      {name === "close" && <CloseIcon color={color} />}
      {name === "refresh" && <RefreshIcon color={color} />}
      {name === "workspace" && <WorkspaceIcon color={color} active={active} />}
      {name === "ward" && <WardIcon color={color} active={active} />}
      {name === "message" && <MessageIcon color={color} active={active} />}
      {name === "profile" && <ProfileIcon color={color} active={active} />}
      {name === "inbox" && <InboxIcon color={color} />}
      {name === "contacts" && <ContactsIcon color={color} />}
      {name === "history" && <HistoryIcon color={color} />}
      {name === "document" && <DocumentIcon color={color} />}
      {name === "chevron" && <ChevronIcon color={color} />}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: "center",
    justifyContent: "center",
    width: 22,
    height: 22,
  },
  centerWrap: {
    alignItems: "center",
    justifyContent: "center",
    width: 18,
    height: 18,
  },
  stroke: {
    height: 1.8,
    borderRadius: 1,
  },
  menuWrap: {
    alignItems: "center",
    justifyContent: "center",
  },
  menuGap: {
    marginTop: 3,
  },
  closeLine: {
    position: "absolute",
  },
  refreshArc: {
    width: 14,
    height: 14,
    borderWidth: 1.8,
    borderRadius: 7,
  },
  refreshHead: {
    position: "absolute",
    top: 1,
    right: 1,
    width: 5,
    height: 5,
    borderTopWidth: 1.8,
    borderRightWidth: 1.8,
    transform: [{ rotate: "20deg" }],
  },
  workspaceShell: {
    width: 16,
    height: 14,
    borderWidth: 1.8,
    borderRadius: 4,
    alignItems: "center",
    justifyContent: "flex-start",
    paddingTop: 2,
  },
  workspaceHead: {
    width: 9,
    height: 1.8,
    borderRadius: 1,
    marginBottom: 2.4,
  },
  workspacePulseWrap: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    height: 6,
  },
  workspacePulseLow: {
    height: 1.6,
    marginTop: 2,
  },
  workspacePulseRise: {
    height: 1.6,
    marginLeft: -0.5,
  },
  workspacePulseFall: {
    height: 1.6,
    marginLeft: -0.8,
  },
  workspacePulseTail: {
    height: 1.6,
    marginLeft: -0.4,
  },
  bedFrame: {
    width: 17,
    height: 13,
    justifyContent: "flex-end",
  },
  bedHead: {
    position: "absolute",
    left: 0,
    bottom: 3,
    width: 3,
    height: 8,
    borderRadius: 1,
  },
  bedBody: {
    marginLeft: 3,
    width: 12,
    height: 6,
    borderWidth: 1.8,
    borderRadius: 2,
  },
  bedLeg: {
    position: "absolute",
    bottom: 0,
    width: 1.8,
    height: 3,
    borderRadius: 1,
  },
  wardCrossV: {
    position: "absolute",
    right: 1.2,
    top: 0.8,
    width: 1.6,
    height: 5.5,
    borderRadius: 1,
  },
  wardCrossH: {
    position: "absolute",
    right: -0.2,
    top: 2.7,
    width: 4.2,
    height: 1.6,
    borderRadius: 1,
  },
  clipboardShell: {
    width: 15,
    height: 16,
    borderWidth: 1.8,
    borderRadius: 3,
    paddingTop: 5,
    paddingHorizontal: 2,
  },
  clipboardLine: {
    alignSelf: "center",
  },
  clipboardLineGap: {
    alignSelf: "center",
    marginTop: 2,
  },
  clipboardClip: {
    position: "absolute",
    top: -1.2,
    left: 4,
    width: 7,
    height: 2.4,
    borderRadius: 2,
  },
  clipboardCrossV: {
    position: "absolute",
    top: 3.3,
    left: 3.2,
    width: 1.5,
    height: 5,
    borderRadius: 1,
  },
  clipboardCrossH: {
    position: "absolute",
    top: 5,
    left: 1.5,
    width: 5,
    height: 1.5,
    borderRadius: 1,
  },
  nurseCap: {
    position: "absolute",
    top: 0,
    width: 15,
    height: 7,
    borderWidth: 1.6,
    borderBottomLeftRadius: 6,
    borderBottomRightRadius: 6,
    borderTopLeftRadius: 4,
    borderTopRightRadius: 4,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeHead: {
    position: "absolute",
    width: 7,
    height: 7,
    borderWidth: 1.8,
    borderRadius: 4,
  },
  profileBody: {
    position: "absolute",
    width: 9,
    height: 4.5,
    borderWidth: 1.8,
    borderTopLeftRadius: 6,
    borderTopRightRadius: 6,
    borderBottomLeftRadius: 3,
    borderBottomRightRadius: 3,
  },
  nurseCapWing: {
    position: "absolute",
    bottom: -1.3,
    width: 4,
    height: 4,
    borderLeftWidth: 1.4,
    borderBottomWidth: 1.4,
    borderBottomLeftRadius: 2,
    backgroundColor: "#ffffff",
  },
  nurseCapCrossV: {
    width: 1.4,
    height: 4,
    borderRadius: 1,
  },
  nurseCapCrossH: {
    position: "absolute",
    width: 4,
    height: 1.4,
    borderRadius: 1,
  },
  inboxBox: {
    width: 16,
    height: 12,
    borderWidth: 1.8,
    borderRadius: 3,
    justifyContent: "center",
    alignItems: "center",
  },
  inboxNotch: {
    width: 8,
    height: 4,
    borderBottomWidth: 1.8,
    borderLeftWidth: 1.8,
    borderRightWidth: 1.8,
    borderBottomLeftRadius: 2,
    borderBottomRightRadius: 2,
  },
  contactHeadSmall: {
    position: "absolute",
    top: 1,
    width: 5,
    height: 5,
    borderWidth: 1.6,
    borderRadius: 3,
  },
  contactBody: {
    position: "absolute",
    bottom: 1,
    width: 14,
    height: 7,
    borderWidth: 1.6,
    borderTopLeftRadius: 6,
    borderTopRightRadius: 6,
    borderBottomLeftRadius: 3,
    borderBottomRightRadius: 3,
  },
  clockFace: {
    width: 14,
    height: 14,
    borderWidth: 1.8,
    borderRadius: 7,
    alignItems: "center",
    justifyContent: "center",
  },
  clockHandLong: {
    position: "absolute",
    height: 5,
    transform: [{ translateY: -2 }],
  },
  clockHandShort: {
    position: "absolute",
    height: 4,
    transform: [{ translateY: 1 }],
  },
  documentShell: {
    width: 14,
    height: 16,
    borderWidth: 1.8,
    borderRadius: 2,
    paddingTop: 5,
    paddingHorizontal: 2,
  },
  documentCorner: {
    position: "absolute",
    top: -1,
    right: -1,
    width: 5,
    height: 5,
    borderLeftWidth: 1.8,
    borderBottomWidth: 1.8,
    backgroundColor: "#fff",
    transform: [{ rotate: "45deg" }],
  },
  documentLine: {
    alignSelf: "center",
  },
  documentLineGap: {
    alignSelf: "center",
    marginTop: 2,
  },
  chevronLeft: {
    position: "absolute",
    right: 7,
  },
  chevronRight: {
    position: "absolute",
    left: 7,
  },
});
