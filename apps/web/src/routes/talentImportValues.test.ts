import { describe, expect, it } from "vitest";
import {
  NOT_SET,
  formatYears,
  importTypeLabel,
  payloadFields,
  sourceRefLabel,
} from "./talentImportValues";

// Restyle batch 6. Every payload below has the real shape the backend
// writes (cited in talentImportValues.ts); every value is fake.

function asMap(payload: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(payloadFields(payload).map((f) => [f.label, f.value]));
}

describe("importTypeLabel", () => {
  it("names every import type, the capability statement upload included", () => {
    expect(importTypeLabel("bulk_resumes")).toBe("Resumes");
    expect(importTypeLabel("employee_master_csv")).toBe("Employee master (CSV)");
    expect(importTypeLabel("employee_master_xlsx")).toBe("Employee master (XLSX)");
    expect(importTypeLabel("certification_documents")).toBe("Certifications");
    expect(importTypeLabel("manual")).toBe("Capability statement");
  });

  it("humanises a type it does not know, and treats prototype keys as unknown", () => {
    expect(importTypeLabel("partner_list")).toBe("Partner list");
    expect(importTypeLabel("constructor")).toBe("Constructor");
  });
});

describe("sourceRefLabel", () => {
  it("reads sheet rows as their row number", () => {
    expect(sourceRefLabel("row_2")).toBe("Row 2");
    expect(sourceRefLabel("row_17")).toBe("Row 17");
  });

  it("counts capability statement items from 1", () => {
    expect(sourceRefLabel("project_0")).toBe("Project 1");
    expect(sourceRefLabel("capability_3")).toBe("Capability 4");
  });

  it("names the parse-failure sentinel and leaves file names as written", () => {
    expect(sourceRefLabel("_batch_parse_failure")).toBe("Whole file");
    expect(sourceRefLabel("resume_avery_quill.docx")).toBe("resume_avery_quill.docx");
    expect(sourceRefLabel("row_two.docx")).toBe("row_two.docx");
  });
});

describe("formatYears", () => {
  it.each([
    [1, "1 year"],
    [7.5, "7.5 years"],
    [0, "0 years"],
    [12, "12 years"],
    [3.25, "3.25 years"],
  ])("%s -> %s", (years, expected) => {
    expect(formatYears(years)).toBe(expected);
  });
});

describe("payloadFields", () => {
  it("lays out a résumé profile in words, with mentions and confidence", () => {
    const fields = payloadFields({
      confidence: 0.92,
      project_mentions: [
        { name: "Harbour Systems", client: "Example Port Authority", year: "2023" },
      ],
      certification_mentions: [
        { name: "PMP", issuer: "PMI", expiry_date: "2027-03-31" },
        { name: "Six Sigma", issuer: null, expiry_date: null },
      ],
      years_experience: 12.5,
      professional_location: "Pune",
      current_title: "Logistics Programme Manager",
      email: null,
      name: "Avery Quill",
    });
    expect(fields.map((f) => f.label)).toEqual([
      "Name",
      "Email",
      "Current title",
      "Location",
      "Experience",
      "Certifications mentioned",
      "Projects mentioned",
      "Extraction confidence",
    ]);
    expect(fields.map((f) => f.value)).toEqual([
      "Avery Quill",
      NOT_SET,
      "Logistics Programme Manager",
      "Pune",
      "12.5 years",
      "PMP (PMI, expires 31 Mar 2027); Six Sigma",
      "Harbour Systems (Example Port Authority, 2023)",
      "92%",
    ]);
    expect(fields.every((f) => f.kind === "text")).toBe(true);
  });

  it("shows a model-written expiry that is not a real day as written", () => {
    expect(
      asMap({ certification_mentions: [{ name: "PMP", issuer: "PMI", expiry_date: "June 2027" }] })[
        "Certifications mentioned"
      ],
    ).toBe("PMP (PMI, expires June 2027)");
  });

  it("says None for an empty mention list", () => {
    expect(asMap({ name: "A", certification_mentions: [], project_mentions: [] })).toEqual({
      Name: "A",
      "Certifications mentioned": "None",
      "Projects mentioned": "None",
    });
  });

  it("lays out an employee master row; its status is the sheet's own text", () => {
    expect(
      asMap({ status: "On leave", role: "Surveyor", name: "B", employee_code: "E-7" }),
    ).toEqual({
      Name: "B",
      "Employee code": "E-7",
      Role: "Surveyor",
      "Status in file": "On leave",
    });
  });

  it("lays out a certification row with its dates as days", () => {
    expect(
      asMap({
        cert_key: "C-1",
        employee_ref: "E-7",
        name: "PMP",
        issuer: "PMI",
        issue_date: "2024-04-01",
        expiry_date: null,
      }),
    ).toEqual({
      Name: "PMP",
      "Certificate key": "C-1",
      "Employee reference": "E-7",
      Issuer: "PMI",
      Issued: "1 Apr 2024",
      Expires: NOT_SET,
    });
  });

  it("lays out a past project, marking its chunk id as an id", () => {
    const fields = payloadFields({
      name: "Harbour Systems",
      client: "Example Port Authority",
      year: "2023",
      description: "Integration programme.",
      source_ref: null,
      chunk_id: "00000001-aaaa-4aaa-8aaa-000000000001",
    });
    expect(fields.map((f) => f.label)).toEqual([
      "Name",
      "Client",
      "Year",
      "Description",
      "Reference",
      "Source chunk",
    ]);
    expect(fields.find((f) => f.key === "chunk_id")?.kind).toBe("id");
  });

  it("gives an empty payload no fields", () => {
    expect(payloadFields({})).toEqual([]);
  });

  it("puts unknown keys last, humanised, and objects in <code> - never [object Object]", () => {
    const fields = payloadFields({
      zeta: true,
      alpha: { nested: 1 },
      name: "C",
      tags: ["a", "b"],
      certification_mentions: [{ issuer: "no name" }],
    });
    expect(fields.map((f) => [f.label, f.value, f.kind])).toEqual([
      ["Name", "C", "text"],
      ["Certifications mentioned", '[{"issuer":"no name"}]', "code"],
      ["Alpha", '{"nested":1}', "code"],
      ["Tags", "a, b", "text"],
      ["Zeta", "Yes", "text"],
    ]);
    expect(fields.some((f) => f.value.includes("[object Object]"))).toBe(false);
  });
});
