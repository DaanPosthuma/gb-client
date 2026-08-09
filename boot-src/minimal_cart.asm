; Minimal valid Game Boy cartridge: just enough header for the boot ROM to accept it
; and jump in. The Nintendo logo bytes and checksums are auto-filled by `rgbfix -v`
; (a standard rgbds toolchain feature - not sourced from any copyrighted ROM dump).
SECTION "Entry", ROM0[$0100]
    nop
    jp Start

SECTION "Header", ROM0[$0104]
    ds $150 - $0104 ; reserved for logo/title/header fields; rgbfix fills logo+checksums

SECTION "Start", ROM0[$0150]
Start:
    di
.loop
    jr .loop
