"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useRegister } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export function RegisterForm() {
  const router = useRouter();
  const register = useRegister();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await register.mutateAsync({
        email,
        password,
        display_name: displayName,
      });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed");
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto w-full max-w-md space-y-4">
      <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
        Create account
      </h1>
      <p className="text-sm text-muted">
        Already registered?{" "}
        <Link href="/login" className="text-accent-soft hover:underline">
          Sign in
        </Link>
      </p>
      <label className="block space-y-1 text-sm">
        <span className="text-muted">Display name</span>
        <input
          type="text"
          required
          autoComplete="name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-foreground outline-none focus:border-accent"
        />
      </label>
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
        <span className="text-muted">Password (min 8 characters)</span>
        <input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
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
        disabled={register.isPending}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-background disabled:opacity-60"
      >
        {register.isPending ? "Creating…" : "Create account"}
      </button>
    </form>
  );
}
