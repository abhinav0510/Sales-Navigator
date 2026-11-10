"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { getSession } from "@/lib/session";

/** Entry point: signed-in users go to their searches, everyone else to sign in. */
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getSession() ? "/searches" : "/login");
  }, [router]);
  return null;
}
