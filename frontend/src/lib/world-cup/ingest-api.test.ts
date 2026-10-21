/**
 * URL contract for the ingest client (audit 53.81).
 *
 * The component tests mock the whole client, so a wrong PREFIX produced
 * 404s in production while every test stayed green. These cases hit the
 * real string builder and pin the mounted paths exactly -- the API_BASE
 * default in tests is "/api", so the fetch URL must start with
 * "/api/events/sports/world-cup".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIGURED_SOURCES, PAYLOAD_SOURCES, ingestApi } from "./ingest-api";

const fetchMock = vi.fn();

/** Plain-object response: ingestFetch reads only ok/text/json. */
function okResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => okResponse({ status: "ok" }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function lastUrl(): Promise<string> {
  const call = fetchMock.mock.calls.at(-1);
  expect(call, "fetch was not called").toBeTruthy();
  return String(call?.[0]);
}

describe("ingest-api URL contract", () => {
  it("mounts the simple GETs under /api/events/sports/world-cup", async () => {
    await ingestApi.status();
    expect(await lastUrl()).toBe("/api/events/sports/world-cup/status");

    await ingestApi.sourceStatus();
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/data/sources/status",
    );

    await ingestApi.facts({ kind: "title", team: "Brazil" });
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/facts?kind=title&team=Brazil",
    );

    await ingestApi.resolve(true, 25);
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/resolve?dry_run=true&limit=25",
    );
  });

  it("builds configured-source action URLs from the registry paths", async () => {
    await ingestApi.runConfigured("data/bundle/url", "import", true);
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/data/bundle/url/import?replace=true",
    );

    await ingestApi.runConfigured("data/bundle/api-football", "test", false);
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/data/bundle/api-football/test",
    );
  });

  it("builds payload-source action URLs from the registry paths", async () => {
    await ingestApi.runPayload("standings", "preview", [{ pos: 1 }]);
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/standings/preview",
    );

    await ingestApi.runPayload("facts", "import", { facts: [] }, false);
    expect(await lastUrl()).toBe(
      "/api/events/sports/world-cup/facts/import?replace=false",
    );
  });

  it("covers every registered source path with its advertised actions", () => {
    // Registry paths and route actions drift independently of the URL
    // builder; this keeps the pair honest so a renamed backend segment
    // cannot pass silently while the mounted prefix above holds. (The
    // actions themselves are pinned by the SourceAction union at compile
    // time -- asserting them here would be a tautology.)
    for (const source of CONFIGURED_SOURCES) {
      expect(source.path, `${source.key} path`).toMatch(
        /^(data(\/source|\/bundle(\/(source|url|feeds|api-football|football-data|sportmonks))?)?)$/,
      );
    }
    for (const source of PAYLOAD_SOURCES) {
      expect(source.path, `${source.key} path`).toMatch(
        /^(data(\/bundle)?|official-csv|matches|match-events|lineups|standings|player-awards|player-status|statistics|facts)$/,
      );
    }
  });
});
