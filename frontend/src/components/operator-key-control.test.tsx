import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import {
  clearOperatorCredentials,
  getOperatorApiKey,
  getOperatorId,
} from "@/lib/operator-credentials";
import { OperatorKeyControl } from "./operator-key-control";

function firstButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector("button");
  if (!(button instanceof HTMLButtonElement)) throw new Error("button not found");
  return button;
}

function submitButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector('button[type="submit"]');
  if (!(button instanceof HTMLButtonElement)) throw new Error("submit button not found");
  return button;
}

function cancelButton(container: HTMLElement): HTMLButtonElement {
  const buttons = [...container.querySelectorAll('button[type="button"]')];
  const button = buttons.find(
    (item) => item !== firstButton(container) && item.getAttribute("aria-label")?.includes("取消"),
  );
  if (!(button instanceof HTMLButtonElement)) throw new Error("cancel button not found");
  return button;
}

describe("OperatorKeyControl", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearOperatorCredentials();
  });

  it("saves a trimmed operator key and operator id", async () => {
    const { container } = render(<OperatorKeyControl />);

    await userEvent.click(firstButton(container));
    await userEvent.type(screen.getByPlaceholderText("API key"), "  secret-key  ");
    await userEvent.type(screen.getByLabelText("Operator"), "  alice  ");
    await userEvent.click(submitButton(container));

    expect(getOperatorApiKey()).toBe("secret-key");
    expect(getOperatorId()).toBe("alice");
    expect(screen.getByLabelText("编辑写接口 API key")).toBeInTheDocument();
  });

  it("restores stored values when editing is cancelled", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "stored-operator");
    const { container } = render(<OperatorKeyControl />);

    await userEvent.click(screen.getByLabelText("编辑写接口 API key"));
    await userEvent.clear(screen.getByPlaceholderText("API key"));
    await userEvent.type(screen.getByPlaceholderText("API key"), "draft-key");
    await userEvent.clear(screen.getByLabelText("Operator"));
    await userEvent.type(screen.getByLabelText("Operator"), "draft-operator");
    await userEvent.click(cancelButton(container));

    expect(getOperatorApiKey()).toBe("stored-key");
    expect(getOperatorId()).toBe("stored-operator");
  });

  it("clears authorization and operator id when empty values are saved", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "stored-operator");
    const { container } = render(<OperatorKeyControl />);

    await userEvent.click(screen.getByLabelText("编辑写接口 API key"));
    await userEvent.clear(screen.getByPlaceholderText("API key"));
    await userEvent.clear(screen.getByLabelText("Operator"));
    await userEvent.click(submitButton(container));

    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
  });

  it("clears credentials via clear button", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "ops");
    render(<OperatorKeyControl />);

    await userEvent.click(screen.getByLabelText("清除写接口授权"));
    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
    expect(screen.getByLabelText("配置写接口 API key")).toBeInTheDocument();
  });
});
