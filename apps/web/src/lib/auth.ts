"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe, login, logout, register, type User } from "@/lib/api";

export const meQueryKey = ["auth", "me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: fetchMe,
    retry: false,
    staleTime: 30_000,
  });
}

export function useRegister() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: register,
    onSuccess: (data) => {
      client.setQueryData(meQueryKey, data.user);
    },
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: login,
    onSuccess: (data) => {
      client.setQueryData(meQueryKey, data.user);
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      client.setQueryData(meQueryKey, null);
      client.removeQueries({ queryKey: meQueryKey });
    },
  });
}

export type { User };
