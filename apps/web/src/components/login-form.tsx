"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useLogin } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export function LoginForm() {
  const router = useRouter();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email, password });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto w-full max-w-md space-y-4">
      <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
        Sign in
      </h1>
      <p className="text-sm text-muted">
        New here?{" "}
        <Link href="/register" className="text-accent-soft hover:underline">
          Create an account
        </Link>
      </p>
      <label className="block space-y-1 text-sm">
        <span className="text-muted">Email</span>
        <input
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-foreground outline-none focus:border-accent"
        />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-muted">Password</span>
        <input
          type="password"
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-foreground outline-none focus:border-accent"
        />
      </label>
      {error && (
        <p className="text-sm text-bad" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={login.isPending}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-background disabled:opacity-60"
      >
        {login.isPending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
