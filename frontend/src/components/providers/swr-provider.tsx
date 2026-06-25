"use client";

import { SWRConfig } from "swr";

const fetcher = async (url: string) => {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.statusText}`);
  }
  return response.json();
};

export function SWRProvider({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: false,
        dedupingInterval: 30_000,
        errorRetryCount: 2,
      }}
    >
      {children}
    </SWRConfig>
  );
}
