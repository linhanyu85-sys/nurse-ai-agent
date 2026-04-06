import React from "react";
import { NavigationContainer } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { RootNavigator } from "./src/navigation/RootNavigator";

type AppErrorBoundaryProps = {
  children: React.ReactNode;
  onReset: () => void;
};

type AppErrorBoundaryState = {
  hasError: boolean;
};

class AppErrorBoundary extends React.Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = {
    hasError: false,
  };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return {
      hasError: true,
    };
  }

  componentDidCatch(error: Error) {
    console.error("App runtime error:", error);
  }

  handleReset = () => {
    this.setState({ hasError: false });
    this.props.onReset();
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <View style={styles.errorPage}>
        <Text style={styles.errorTitle}>应用刚刚遇到异常</Text>
        <Text style={styles.errorText}>已经拦截这次渲染错误，避免直接掉回 Expo 错误页。</Text>
        <Text style={styles.errorText}>请点“重新进入应用”继续，如果问题再次出现我会继续顺着日志修。</Text>
        <Pressable style={styles.errorButton} onPress={this.handleReset}>
          <Text style={styles.errorButtonText}>重新进入应用</Text>
        </Pressable>
      </View>
    );
  }
}

export default function App() {
  const [appVersion, setAppVersion] = React.useState(0);

  return (
    <SafeAreaProvider>
      <NavigationContainer key={appVersion}>
        <StatusBar style="dark" />
        <AppErrorBoundary onReset={() => setAppVersion((value) => value + 1)}>
          <RootNavigator />
        </AppErrorBoundary>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  errorPage: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: 24,
    backgroundColor: "#f7fafb",
  },
  errorTitle: {
    fontSize: 24,
    fontWeight: "800",
    color: "#11353f",
    marginBottom: 12,
  },
  errorText: {
    fontSize: 15,
    lineHeight: 24,
    color: "#47636b",
    marginBottom: 8,
  },
  errorButton: {
    alignSelf: "flex-start",
    marginTop: 16,
    borderRadius: 999,
    paddingHorizontal: 18,
    paddingVertical: 12,
    backgroundColor: "#0f5c7d",
  },
  errorButtonText: {
    color: "#ffffff",
    fontSize: 15,
    fontWeight: "700",
  },
});
