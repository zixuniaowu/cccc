import { describe, expect, it } from "vitest";
import { buildHelpMarkdown, parseHelpMarkdown, updateActorHelpNote, updatePetHelpNote } from "./helpMarkdown";

describe("helpMarkdown pet persona", () => {
  it("parses and rebuilds the pet block without dropping other sections", () => {
    const markdown = `
Shared guidance.

## @role: foreman

Foreman note.

## @role: peer

Peer note.

## @pet

Pet coordinator note.

## @actor: peer-1

Actor note.
`.trim();

    const parsed = parseHelpMarkdown(markdown);
    expect(parsed.common).toBe("Shared guidance.");
    expect(parsed.foreman).toBe("Foreman note.");
    expect(parsed.peer).toBe("Peer note.");
    expect(parsed.pet).toBe("Pet coordinator note.");
    expect(parsed.actorNotes["peer-1"]).toBe("Actor note.");

    const rebuilt = buildHelpMarkdown({
      common: parsed.common,
      foreman: parsed.foreman,
      peer: parsed.peer,
      pet: parsed.pet,
      actorNotes: parsed.actorNotes,
      actorOrder: ["peer-1"],
      extraTaggedBlocks: parsed.extraTaggedBlocks,
    });

    expect(rebuilt).toContain("## @pet");
    expect(rebuilt).toContain("Pet coordinator note.");
    expect(rebuilt).toContain("## @actor: peer-1");
  });

  it("preserves pet persona when actor notes are updated", () => {
    const markdown = `
## @pet

Keep the cat low-noise.

## @actor: peer-1

Old actor note.
`.trim();

    const updated = updateActorHelpNote(markdown, "peer-1", "New actor note.", ["peer-1"]);
    const parsed = parseHelpMarkdown(updated);

    expect(parsed.pet).toBe("Keep the cat low-noise.");
    expect(parsed.actorNotes["peer-1"]).toBe("New actor note.");
  });

  it("updates the pet block without dropping actor notes", () => {
    const markdown = `
## @actor: peer-1

Actor note.

## @pet

Old pet note.
`.trim();

    const updated = updatePetHelpNote(markdown, "New pet note.", ["peer-1"]);
    const parsed = parseHelpMarkdown(updated);

    expect(parsed.pet).toBe("New pet note.");
    expect(parsed.actorNotes["peer-1"]).toBe("Actor note.");
  });
});
