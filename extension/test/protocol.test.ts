import { describe, expect, test } from "vitest";
import * as p from "../src/protocol";

describe("frames", () => {
  test("roundtrip", () => {
    const pcm = Int16Array.from({ length: p.FRAME_SAMPLES }, (_, i) => i - 800);
    const buf = p.encodeFrame({ sampleIdx: 123_456_789, mediaTime: 4321.25, pcm, discontinuity: true });
    expect(buf.byteLength).toBe(3220);
    const back = p.decodeFrame(buf);
    expect(back.sampleIdx).toBe(123_456_789);
    expect(back.mediaTime).toBe(4321.25);
    expect(back.discontinuity).toBe(true);
    expect(Array.from(back.pcm)).toEqual(Array.from(pcm));
  });

  test("byte layout matches the Python encoder", () => {
    // Same vector as server/tests/test_protocol.py::test_frame_layout_is_little_endian
    const buf = new Uint8Array(
      p.encodeFrame({ sampleIdx: 1, mediaTime: 0.5, pcm: Int16Array.of(1, -2), discontinuity: false }),
    );
    expect(Array.from(buf.slice(0, 4))).toEqual([1, 0, 0, 0]);
    expect(Array.from(buf.slice(4, 12))).toEqual([1, 0, 0, 0, 0, 0, 0, 0]);
    expect(Array.from(buf.slice(20))).toEqual([1, 0, 0xfe, 0xff]);
  });
});

describe("message guards", () => {
  test("accepts valid server messages", () => {
    expect(p.parseServerMessage('{"type":"final","seg_id":1,"ja":"はい","t0":1,"t1":2,"asr_ms":100}')).not.toBeNull();
    expect(p.parseServerMessage('{"type":"ready","session_id":"s","asr_model":"m","mt_model":null,"sample_rate":16000}')).not.toBeNull();
    expect(p.parseServerMessage('{"type":"error","code":"mt_timeout","message":"x","fatal":false,"seg_id":null}')).not.toBeNull();
  });

  test("rejects malformed messages", () => {
    expect(p.parseServerMessage("not json")).toBeNull();
    expect(p.parseServerMessage('{"type":"final","seg_id":1}')).toBeNull();
    expect(p.parseServerMessage('{"type":"final","seg_id":1.5,"ja":"","t0":1,"t1":2,"asr_ms":1}')).toBeNull();
    expect(p.parseServerMessage('{"type":"hello","protocol_version":1,"video_id":"x"}')).toBeNull();
    expect(p.parseServerMessage('{"type":"error","code":"nope","message":"x"}')).toBeNull();
    expect(p.parseServerMessage('{"type":"ready","session_id":"s","asr_model":"m"}')).toBeNull();
  });

  test("client guard", () => {
    expect(p.isClientMessage({ type: "hello", protocol_version: 1, video_id: "abc" })).toBe(true);
    expect(p.isClientMessage({ type: "pong", ts: 1 })).toBe(false);
  });
});
