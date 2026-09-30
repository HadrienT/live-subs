// Fails as soon as protocol.ts and server/src/livesubs/protocol.py diverge.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import * as p from "../src/protocol";

const serverDir = fileURLToPath(new URL("../../server/src/livesubs/", import.meta.url));
const pySource = readFileSync(serverDir + "protocol.py", "utf8");
const pySchema = JSON.parse(readFileSync(serverDir + "protocol.schema.json", "utf8"));

function pyConstant(name: string): number {
  const match = new RegExp(`^${name}\\s*=\\s*([0-9_x]+)`, "m").exec(pySource);
  if (!match?.[1]) throw new Error(`${name} not found in protocol.py`);
  return Number(match[1].replaceAll("_", ""));
}

describe("protocol drift", () => {
  test("constants", () => {
    expect(p.PROTOCOL_VERSION).toBe(pyConstant("PROTOCOL_VERSION"));
    expect(p.PROTOCOL_VERSION).toBe(pySchema.protocol_version);
    expect(p.SAMPLE_RATE).toBe(pyConstant("SAMPLE_RATE"));
    expect(p.FRAME_SAMPLES).toBe(pyConstant("FRAME_SAMPLES"));
    expect(p.FRAME_HEADER_SIZE).toBe(pySchema.frame.header_size);
    expect(p.FRAME_KIND_AUDIO).toBe(pySchema.frame.kind_audio);
    expect(p.FLAG_DISCONTINUITY).toBe(pySchema.frame.flag_discontinuity);
    expect([...p.ERROR_CODES]).toEqual(pySchema.error_codes);
  });

  test("same message types on both sides", () => {
    expect(Object.keys(p.SCHEMA).sort()).toEqual(Object.keys(pySchema.messages).sort());
  });

  test.each(Object.keys(pySchema.messages))("message %s has the same fields", (type) => {
    const ts = p.SCHEMA[type as p.MessageType];
    expect(ts).toBeDefined();
    expect({ direction: ts.direction, fields: ts.fields }).toEqual(pySchema.messages[type]);
  });

});
