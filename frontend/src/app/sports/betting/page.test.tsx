import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import BettingHubPage from "./page";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const useBettingCatalog = vi.fn();
const useBettingStatus = vi.fn();

vi.mock("@/lib/sports-api", () => ({
  useBettingCatalog: () => useBettingCatalog(),
  useBettingStatus: () => useBettingStatus(),
}));

describe("BettingHubPage", () => {
  beforeEach(() => {
    useBettingCatalog.mockReset();
    useBettingStatus.mockReset();
  });

  it("falls back to static catalog without flag strip or runtime status", () => {
    useBettingCatalog.mockReturnValue({
      data: undefined,
      error: new Error("offline"),
      isLoading: false,
    });
    useBettingStatus.mockReturnValue({ data: undefined });

    render(<BettingHubPage />);

    expect(screen.getByRole("heading", { name: "竞猜中心" })).toBeInTheDocument();
    expect(screen.getByTestId("catalog-source-hint")).toHaveTextContent(
      /使用本地静态 catalog/,
    );
    expect(screen.queryByTestId("hub-flag-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("hub-runtime-status")).not.toBeInTheDocument();
  });

  it("shows flag strip with LoL dry-run when catalog flags are live", () => {
    useBettingCatalog.mockReturnValue({
      data: {
        version: 1,
        competitions: [],
        tools: [],
        sections: {},
        flags: {
          kernel_prediction_enabled: true,
          epl_data_enabled: true,
          phase2_leagues_enabled: true,
          phase_lol_enabled: true,
          lol_dry_run_import: true,
        },
      },
      error: undefined,
      isLoading: false,
    });
    useBettingStatus.mockReturnValue({ data: undefined });

    render(<BettingHubPage />);

    expect(screen.getByTestId("catalog-source-hint")).toHaveTextContent(
      /已合并后端 catalog/,
    );
    const strip = screen.getByTestId("hub-flag-strip");
    expect(strip).toHaveTextContent(/Kernel=ON/);
    expect(strip).toHaveTextContent(/EPL=ON/);
    expect(strip).toHaveTextContent(/五大联赛=ON/);
    expect(strip).toHaveTextContent(/LoL=ON/);
    expect(strip).toHaveTextContent(/dry-run=ON/);
    expect(screen.queryByTestId("hub-runtime-status")).not.toBeInTheDocument();
  });

  it("shows runtime prefixes and blocked LoL vendor when status is available", () => {
    useBettingCatalog.mockReturnValue({
      data: {
        version: 1,
        competitions: [],
        tools: [],
        sections: {},
        flags: {
          kernel_prediction_enabled: true,
          epl_data_enabled: false,
          phase2_leagues_enabled: false,
          phase_lol_enabled: false,
          lol_dry_run_import: false,
        },
      },
      error: undefined,
      isLoading: false,
    });
    useBettingStatus.mockReturnValue({
      data: {
        version: 1,
        kernel_ready: true,
        registered_prefixes: ["epl-", "lol-"],
        kernel_error: null,
        lol: {
          schedule_vendor: "grid",
          effective_schedule_vendor: "null",
          schedule_source_blocked: true,
        },
      },
    });

    render(<BettingHubPage />);

    const runtime = screen.getByTestId("hub-runtime-status");
    expect(runtime).toHaveTextContent(/Kernel ready/);
    expect(runtime).toHaveTextContent(/prefixes: epl-, lol-/);
    expect(runtime).toHaveTextContent(/LoL vendor=grid→null \(blocked\)/);
    expect(screen.getByTestId("hub-flag-strip")).toHaveTextContent(/LoL=OFF/);
    expect(screen.getByTestId("hub-flag-strip")).not.toHaveTextContent(/dry-run=/);
  });
});
