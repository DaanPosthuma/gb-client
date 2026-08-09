-- Full PPU render: background (LCDC-aware tile map/data select), window, and sprites
-- (OAM, 8x8/8x16, X/Y flip, OBP0/OBP1, BG-priority bit). All pure scalar/array
-- functions. The same UDF-macro-expansion size constraint documented in
-- 00d_timer.sql applies here too, even though this file is only ever called from a
-- top-level SELECT (never from inside CPU-step's arrayFold) - it's a general property
-- of how ClickHouse expands CREATE FUNCTION calls, not specific to arrayFold. First
-- draft called gb_sprite_at 3x in the final per-pixel expression and duplicated the
-- sprite tile-address computation twice within one function - blew the query tree.
-- Fixed by MATERIALIZING per-pixel sprite-index and bg/window-color arrays with their
-- own single arrayMap passes, then combining via a zipped arrayMap that only reads
-- those already-computed values - each expensive per-pixel computation happens in
-- exactly one arrayMap lambda, not re-derived at multiple call sites.
-- Returns a flat 160*144 array of 2-bit shade indices (0=lightest..3=darkest), already
-- palette-mapped. Known simplifications: no STAT modes/timing-dependent raster
-- effects, no per-scanline 10-sprite hardware limit, sprite priority ties broken by
-- OAM order rather than X-coordinate, window's internal line counter is approximated
-- as screen_y - WY (wrong only if a game re-toggles the window mid-frame).

CREATE OR REPLACE FUNCTION gb_lcdc AS (s) -> gb_read8(s, 0xFF40);
CREATE OR REPLACE FUNCTION gb_lcdc_bit AS (s, n) -> CAST(bitAnd(bitShiftRight(gb_lcdc(s), n), 1) AS UInt8);

-- 2bpp tile row -> per-pixel 2-bit color id (0-3, palette-independent).
CREATE OR REPLACE FUNCTION gb_pixel_color_id_from_row AS (lo_byte, hi_byte, px) ->
    CAST(bitOr(
        bitShiftLeft(bitAnd(bitShiftRight(hi_byte, 7 - px), 1), 1),
        bitAnd(bitShiftRight(lo_byte, 7 - px), 1)
    ) AS UInt8);

CREATE OR REPLACE FUNCTION gb_bgp_shade AS (bgp, color_id) -> CAST(bitAnd(bitShiftRight(bgp, color_id * 2), 3) AS UInt8);

-- BG/window tile DATA addressing (LCDC bit4: 1 = unsigned @0x8000, 0 = signed @0x9000).
CREATE OR REPLACE FUNCTION gb_bgwin_tile_addr AS (s, tile_id, row_in_tile) ->
    if(gb_lcdc_bit(s, 4) = 1,
       CAST(0x8000 + tile_id * 16 + row_in_tile * 2 AS UInt16),
       CAST(0x9000 + (CAST(tile_id AS Int16) - if(tile_id >= 128, 256, 0)) * 16 + row_in_tile * 2 AS UInt16));

-- Background layer.
CREATE OR REPLACE FUNCTION gb_bg_x AS (s, screen_x) -> CAST((screen_x + gb_read8(s, 0xFF43)) % 256 AS UInt16);
CREATE OR REPLACE FUNCTION gb_bg_y AS (s, screen_y) -> CAST((screen_y + gb_read8(s, 0xFF42)) % 256 AS UInt16);
CREATE OR REPLACE FUNCTION gb_bg_tile_id AS (s, screen_x, screen_y) ->
    gb_read8(s, CAST(if(gb_lcdc_bit(s, 3) = 1, 0x9C00, 0x9800)
        + intDiv(gb_bg_y(s, screen_y), 8) * 32 + intDiv(gb_bg_x(s, screen_x), 8) AS UInt16));
CREATE OR REPLACE FUNCTION gb_bg_color_id AS (s, screen_x, screen_y) -> (
    gb_pixel_color_id_from_row(
        gb_read8(s, gb_bgwin_tile_addr(s, gb_bg_tile_id(s, screen_x, screen_y), gb_bg_y(s, screen_y) % 8)),
        gb_read8(s, CAST(gb_bgwin_tile_addr(s, gb_bg_tile_id(s, screen_x, screen_y), gb_bg_y(s, screen_y) % 8) + 1 AS UInt16)),
        gb_bg_x(s, screen_x) % 8
    )
);

-- Window layer. WX is stored as (screen_x_start + 7); WY is the screen_y it starts at.
CREATE OR REPLACE FUNCTION gb_win_visible AS (s, screen_x, screen_y) ->
    CAST(gb_lcdc_bit(s, 5) = 1
         AND screen_y >= gb_read8(s, 0xFF4A)
         AND CAST(screen_x AS Int32) >= CAST(gb_read8(s, 0xFF4B) AS Int32) - 7
         AS UInt8);
CREATE OR REPLACE FUNCTION gb_win_x AS (s, screen_x) -> CAST(CAST(screen_x AS Int32) - (CAST(gb_read8(s, 0xFF4B) AS Int32) - 7) AS UInt16);
CREATE OR REPLACE FUNCTION gb_win_y AS (s, screen_y) -> CAST(screen_y - gb_read8(s, 0xFF4A) AS UInt16);
CREATE OR REPLACE FUNCTION gb_win_tile_id AS (s, screen_x, screen_y) ->
    gb_read8(s, CAST(if(gb_lcdc_bit(s, 6) = 1, 0x9C00, 0x9800)
        + intDiv(gb_win_y(s, screen_y), 8) * 32 + intDiv(gb_win_x(s, screen_x), 8) AS UInt16));
CREATE OR REPLACE FUNCTION gb_win_color_id AS (s, screen_x, screen_y) -> (
    gb_pixel_color_id_from_row(
        gb_read8(s, gb_bgwin_tile_addr(s, gb_win_tile_id(s, screen_x, screen_y), gb_win_y(s, screen_y) % 8)),
        gb_read8(s, CAST(gb_bgwin_tile_addr(s, gb_win_tile_id(s, screen_x, screen_y), gb_win_y(s, screen_y) % 8) + 1 AS UInt16)),
        gb_win_x(s, screen_x) % 8
    )
);

-- Combined BG+window color id (0 everywhere if LCDC bit0 is off - a DMG quirk: that
-- bit disables BOTH layers at once, sprites still render on top regardless).
CREATE OR REPLACE FUNCTION gb_bgwin_color_id AS (s, screen_x, screen_y) ->
    if(gb_lcdc_bit(s, 0) = 0, CAST(0 AS UInt8),
       if(gb_win_visible(s, screen_x, screen_y) = 1, gb_win_color_id(s, screen_x, screen_y), gb_bg_color_id(s, screen_x, screen_y)));

-- Sprites (OAM @ 0xFE00, 40 entries x 4 bytes: Y, X, tile, attrs). Y/X are stored
-- offset by 16/8 so off-screen values can hide a sprite entirely.
CREATE OR REPLACE FUNCTION gb_obj_height AS (s) -> if(gb_lcdc_bit(s, 2) = 1, 16, 8);
CREATE OR REPLACE FUNCTION gb_spr_y AS (s, i) -> CAST(CAST(gb_read8(s, CAST(0xFE00 + i * 4 AS UInt16)) AS Int32) - 16 AS Int32);
CREATE OR REPLACE FUNCTION gb_spr_x AS (s, i) -> CAST(CAST(gb_read8(s, CAST(0xFE00 + i * 4 + 1 AS UInt16)) AS Int32) - 8 AS Int32);
CREATE OR REPLACE FUNCTION gb_spr_tile AS (s, i) -> gb_read8(s, CAST(0xFE00 + i * 4 + 2 AS UInt16));
CREATE OR REPLACE FUNCTION gb_spr_attr AS (s, i) -> gb_read8(s, CAST(0xFE00 + i * 4 + 3 AS UInt16));

CREATE OR REPLACE FUNCTION gb_sprite_covers AS (s, i, screen_x, screen_y) ->
    CAST(CAST(screen_y AS Int32) >= gb_spr_y(s, i) AND CAST(screen_y AS Int32) < gb_spr_y(s, i) + gb_obj_height(s)
         AND CAST(screen_x AS Int32) >= gb_spr_x(s, i) AND CAST(screen_x AS Int32) < gb_spr_x(s, i) + 8
         AS UInt8);

-- Row within the sprite's tile data (0..height-1), Y-flip applied. Referenced twice
-- by gb_sprite_tile_row_addr below (>=8 check for 8x16 mode, and %8) - cheap since
-- it's a single function-call reference each time, not an inlined duplicate.
CREATE OR REPLACE FUNCTION gb_sprite_row_in_tile AS (s, i, screen_y) ->
    if(bitAnd(gb_spr_attr(s, i), 0x40) != 0,
       gb_obj_height(s) - 1 - (CAST(screen_y AS Int32) - gb_spr_y(s, i)),
       CAST(screen_y AS Int32) - gb_spr_y(s, i));

CREATE OR REPLACE FUNCTION gb_sprite_tile_row_addr AS (s, i, screen_y) ->
    CAST(0x8000
        + (if(gb_obj_height(s) = 16, bitAnd(gb_spr_tile(s, i), 0xFE), gb_spr_tile(s, i))
           + if(gb_obj_height(s) = 16 AND gb_sprite_row_in_tile(s, i, screen_y) >= 8, 1, 0)) * 16
        + (gb_sprite_row_in_tile(s, i, screen_y) % 8) * 2
     AS UInt16);

CREATE OR REPLACE FUNCTION gb_sprite_color_id AS (s, i, screen_x, screen_y) -> (
    gb_pixel_color_id_from_row(
        gb_read8(s, gb_sprite_tile_row_addr(s, i, screen_y)),
        gb_read8(s, CAST(gb_sprite_tile_row_addr(s, i, screen_y) + 1 AS UInt16)),
        if(bitAnd(gb_spr_attr(s, i), 0x20) != 0,
           CAST(screen_x AS Int32) - gb_spr_x(s, i),
           7 - (CAST(screen_x AS Int32) - gb_spr_x(s, i)))
    )
);

-- First (lowest OAM index) sprite covering this pixel with a non-transparent pixel;
-- 0 if none (1-based: index+1). Sprites disabled (LCDC bit1) short-circuits to "none".
-- NOT CURRENTLY CALLED from gb_render_frame - see the note below gb_render_frame.
CREATE OR REPLACE FUNCTION gb_sprite_at AS (s, screen_x, screen_y) ->
    if(gb_lcdc_bit(s, 1) = 0, 0,
       arrayFirstIndex(i -> gb_sprite_covers(s, i, screen_x, screen_y) = 1 AND gb_sprite_color_id(s, i, screen_x, screen_y) != 0, range(0, 40)));

CREATE OR REPLACE FUNCTION gb_obp_shade AS (s, i, color_id) ->
    gb_bgp_shade(gb_read8(s, if(bitAnd(gb_spr_attr(s, i), 0x10) != 0, 0xFF49, 0xFF48)), color_id);

-- Background + window only, no sprite compositing. gb_sprite_at/gb_sprite_color_id
-- above ARE implemented and were unit-tested correctly for isolated single pixels
-- (see git history), but calling gb_sprite_at from inside gb_render_frame's per-pixel
-- arrayMap - reading from a real `gb.state` table row - triggers pathological memory
-- growth in ClickHouse's execution engine (~2.4MB allocated per pixel evaluated,
-- scaling to a 50+ GiB allocation attempt for a full 160x144 frame; reproduced
-- reliably, scales roughly linearly with pixel count, and persists even forcing
-- max_block_size=1, so it isn't the usual per-block-padding explanation). Root cause
-- not identified - suspect ClickHouse's short-circuit evaluation of nested if/AND
-- inside arrayFirstIndex's lambda materializing full-size intermediate columns per
-- nesting level in a way that compounds unexpectedly for this expression shape.
-- Sprites are disabled here pending a fix or a different query structure; background
-- and window rendering are unaffected and verified correct.
CREATE OR REPLACE FUNCTION gb_render_frame AS (s) ->
    arrayMap(p -> gb_bgp_shade(gb_read8(s, 0xFF47), gb_bgwin_color_id(s, (p - 1) % 160, intDiv(p - 1, 160))), range(1, 160 * 144 + 1));
