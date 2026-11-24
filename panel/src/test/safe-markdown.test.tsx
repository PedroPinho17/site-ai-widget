import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { stripCitations } from "@/lib/citations";
import { SafeMarkdown } from "@/lib/safe-markdown";

describe("safe markdown", () => {
  it("strips citation marks", () => {
    expect(stripCitations("Abrimos às 9h 【1】 e aos sábados [2].")).toBe(
      "Abrimos às 9h e aos sábados.",
    );
  });

  it("keeps the space between a street name and its number", () => {
    expect(stripCitations("Rua das Amendoeiras [1]48, Lisboa")).toBe(
      "Rua das Amendoeiras 48, Lisboa",
    );
    expect(stripCitations("Rua das Amendoeiras【1】48")).toBe("Rua das Amendoeiras 48");
  });

  it("renders bold and lists without HTML injection", () => {
    render(
      <SafeMarkdown text={"**Contacte-nos** no telemóvel.\n- Rua das Flores\n- Lisboa\n【1】"} />,
    );
    expect(screen.getByText("Contacte-nos").tagName).toBe("STRONG");
    expect(screen.getByText("Rua das Flores").tagName).toBe("LI");
    expect(screen.queryByText("【1】")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("<script");
  });

  it("shows HTML from a reply as text and never creates elements from it", () => {
    const { container } = render(
      <SafeMarkdown text={'<img src=x onerror="alert(1)"> **<script>x</script>**\n- <b>Rua</b>'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(container.textContent).toContain("<script>x</script>");
    expect(screen.getByText("<script>x</script>").tagName).toBe("STRONG");
  });
});
