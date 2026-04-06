import { create } from "zustand";

import type { Patient, UserInfo } from "../types";

type AppState = {
  token: string | null;
  user: UserInfo | null;
  selectedDepartmentId: string;
  selectedPatient: Patient | null;
  setAuth: (token: string, user: UserInfo) => void;
  logout: () => void;
  setDepartment: (departmentId: string) => void;
  setSelectedPatient: (patient: Patient | null) => void;
};

export const useAppStore = create<AppState>((set) => ({
  token: null,
  user: null,
  selectedDepartmentId: "dep-card-01",
  selectedPatient: null,
  setAuth: (token, user) => set({ token, user }),
  logout: () => set({ token: null, user: null, selectedPatient: null }),
  setDepartment: (selectedDepartmentId) => set({ selectedDepartmentId }),
  setSelectedPatient: (selectedPatient) => set({ selectedPatient })
}));
