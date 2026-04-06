import React, { useEffect, useState } from "react";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { AppGlyph } from "../components/AppGlyph";
import { useAppStore } from "../store/appStore";
import { BrandSplashScreen } from "../screens/BrandSplashScreen";
import { AIWorkspaceScreen } from "../screens/AIWorkspaceScreen";
import { DocumentEditorScreen } from "../screens/DocumentEditorScreen";
import { LoginScreen } from "../screens/LoginScreen";
import { MessageThreadScreen } from "../screens/MessageThreadScreen";
import { PatientDetailScreen } from "../screens/PatientDetailScreen";
import { ProfileScreen } from "../screens/ProfileScreen";
import { RegisterScreen } from "../screens/RegisterScreen";
import { TaskHubScreen } from "../screens/TaskHubScreen";
import { WardOverviewScreen } from "../screens/WardOverviewScreen";
import type { DocumentDraft } from "../types";
import { colors } from "../theme";

export type RootStackParamList = {
  Login: undefined;
  Register: undefined;
  MainTabs: undefined;
  PatientDetail: { patientId: string; bedNo?: string };
  DocumentEditor: { patientId: string; bedNo?: string; draftId: string; initialDraft?: DocumentDraft };
  MessageThread: { kind: "ai" | "direct"; sessionId?: string; title?: string; contactUserId?: string };
};

export type MainTabParamList = {
  Workspace: undefined;
  Ward: undefined;
  Tasks: undefined;
  Profile: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<MainTabParamList>();

const tabMeta: Record<
  keyof MainTabParamList,
  { label: string; icon: "workspace" | "ward" | "message" | "profile" }
> = {
  Workspace: { label: "\u5de5\u4f5c\u53f0", icon: "workspace" },
  Ward: { label: "\u75c5\u533a", icon: "ward" },
  Tasks: { label: "\u534f\u4f5c", icon: "message" },
  Profile: { label: "\u6211\u7684", icon: "profile" },
};

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => {
        const meta = tabMeta[route.name as keyof MainTabParamList];
        return {
          headerShown: false,
          tabBarActiveTintColor: colors.primary,
          tabBarInactiveTintColor: colors.subText,
          tabBarStyle: {
            height: 64,
            paddingTop: 8,
            paddingBottom: 8,
            borderTopColor: "#d7e0e2",
            backgroundColor: "#ffffff",
          },
          tabBarLabelStyle: {
            fontSize: 12,
            fontWeight: "700",
          },
          tabBarIconStyle: {
            marginBottom: 2,
          },
          tabBarIcon: ({ color, focused }) => (
            <AppGlyph name={meta.icon} size={focused ? 18 : 17} color={color} active={focused} />
          ),
          tabBarLabel: meta.label,
        };
      }}
    >
      <Tab.Screen name="Workspace" component={AIWorkspaceScreen} />
      <Tab.Screen name="Ward" component={WardOverviewScreen} />
      <Tab.Screen name="Tasks" component={TaskHubScreen} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}

export function RootNavigator() {
  const token = useAppStore((state) => state.token);
  const [showSplash, setShowSplash] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setShowSplash(false), 900);
    return () => clearTimeout(timer);
  }, []);

  if (showSplash) {
    return <BrandSplashScreen />;
  }

  if (!token) {
    return (
      <Stack.Navigator screenOptions={{ contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="Login" component={LoginScreen} options={{ headerShown: false }} />
        <Stack.Screen name="Register" component={RegisterScreen} options={{ headerShown: false }} />
      </Stack.Navigator>
    );
  }

  return (
    <Stack.Navigator screenOptions={{ contentStyle: { backgroundColor: colors.bg } }}>
      <Stack.Screen name="MainTabs" component={MainTabs} options={{ headerShown: false }} />
      <Stack.Screen
        name="PatientDetail"
        component={PatientDetailScreen}
        options={{
          title: "\u75c5\u4f8b\u8be6\u60c5",
          headerShadowVisible: false,
          headerStyle: { backgroundColor: "#f7fafb" },
        }}
      />
      <Stack.Screen
        name="DocumentEditor"
        component={DocumentEditorScreen}
        options={{
          title: "\u6807\u51c6\u6587\u4e66\u7f16\u8f91",
          headerShadowVisible: false,
          headerStyle: { backgroundColor: "#f7fafb" },
        }}
      />
      <Stack.Screen
        name="MessageThread"
        component={MessageThreadScreen}
        options={{
          title: "\u6d88\u606f",
          headerShadowVisible: false,
          headerStyle: { backgroundColor: "#f7fafb" },
        }}
      />
    </Stack.Navigator>
  );
}
