-- Background-only PPU render: no window/sprites/STAT modes, since the boot ROM never
-- uses them. All pure scalar functions - arrayMap's lambda is defined once and applied
-- per-pixel at runtime (same safe pattern as arrayFold), no state chaining involved.
-- Returns a flat 160*144 array of 2-bit shade indices (0=lightest .. 3=darkest), already
-- passed through BGP; the caller maps shades to actual pixel values for display.

CREATE OR REPLACE FUNCTION gb_bg_x AS (s, screen_x) -> CAST((screen_x + gb_read8(s, 0xFF43)) % 256 AS UInt16);
CREATE OR REPLACE FUNCTION gb_bg_y AS (s, screen_y) -> CAST((screen_y + gb_read8(s, 0xFF42)) % 256 AS UInt16);

CREATE OR REPLACE FUNCTION gb_bg_tile_id AS (s, screen_x, screen_y) ->
    gb_read8(s, CAST(0x9800 + intDiv(gb_bg_y(s, screen_y), 8) * 32 + intDiv(gb_bg_x(s, screen_x), 8) AS UInt16));

CREATE OR REPLACE FUNCTION gb_bg_tile_row_addr AS (s, screen_x, screen_y) ->
    CAST(0x8000 + gb_bg_tile_id(s, screen_x, screen_y) * 16 + (gb_bg_y(s, screen_y) % 8) * 2 AS UInt16);

CREATE OR REPLACE FUNCTION gb_bg_color_id AS (s, screen_x, screen_y) -> (
    bitOr(
        bitShiftLeft(bitAnd(bitShiftRight(gb_read8(s, CAST(gb_bg_tile_row_addr(s, screen_x, screen_y) + 1 AS UInt16)), 7 - (gb_bg_x(s, screen_x) % 8)), 1), 1),
        bitAnd(bitShiftRight(gb_read8(s, gb_bg_tile_row_addr(s, screen_x, screen_y)), 7 - (gb_bg_x(s, screen_x) % 8)), 1)
    )
);

CREATE OR REPLACE FUNCTION gb_bgp_shade AS (bgp, color_id) -> CAST(bitAnd(bitShiftRight(bgp, color_id * 2), 3) AS UInt8);

CREATE OR REPLACE FUNCTION gb_bg_pixel_shade AS (s, screen_x, screen_y) ->
    gb_bgp_shade(gb_read8(s, 0xFF47), gb_bg_color_id(s, screen_x, screen_y));

CREATE OR REPLACE FUNCTION gb_render_frame AS (s) ->
    arrayMap(p -> gb_bg_pixel_shade(s, (p - 1) % 160, intDiv(p - 1, 160)), range(1, 160 * 144 + 1));
