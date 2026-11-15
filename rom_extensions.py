"""
RetroVault Toolkit - known ROM / music / media / companion file extensions.

This is the single list the File Sorter (api.py, Module 2) uses to decide
what is a "known" file.  Edit the tables below, then run

    python rom_extensions.py

to regenerate ROM_Extensions_Reference.txt (the human-readable copy).

Table format: one line per entry, "ext [ext ...] | description".
Lines starting with "#" start a new group heading.  Extensions are
matched case-insensitively and without the leading dot.

Sources: the original ROM_EXTS list, the extensions found across the
!DATS collection (No-Intro, Redump, TOSEC, VGM Archive, MultiMedia ...),
ES-DE es_systems.xml and the libretro core-info supported_extensions.
"""
import re
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════
#  ROM / disk / tape / disc images, firmware, saves, patches
# ════════════════════════════════════════════════════════════════════════
ROM_TABLE = r"""
# Atari (2600, 5200, 7800, 8-bit, Lynx, Jaguar, ST)
a26           | Atari 2600
a52           | Atari 5200
a78           | Atari 7800
atr           | Atari 8-bit disk
xex           | Xbox 360 / Atari 8-bit
xfd           | Atari 8-bit disk
cas           | Atari 8-bit / Dragon / MSX tape
car           | Atari 8-bit cartridge
cart          | Atari 5200/8-bit cartridge
dcm           | Atari 8-bit DCM disk
lnx           | Atari Lynx
lyx           | Atari Lynx
jag           | Atari Jaguar
j64           | Atari Jaguar
atx           | Atari 8-bit disk (extended)
abs           | Atari Jaguar ABS
cof           | Atari Jaguar COFF
bjl           | Atari Jaguar BJL
st            | Atari ST disk image / Sufami Turbo
stx           | Atari ST Pasti flux
stt           | Atari ST disk (extended)
msa           | Atari ST MSA disk
tos           | Atari ST TOS image
ddp           | Atari ST data dump

# Nintendo - NES / Famicom
nes           | Nintendo NES
fc            | Famicom
unf unif      | NES (UNIF)
fds           | Famicom Disk System
qd            | Famicom Quick Disk
# Nintendo - SNES / Super Famicom
sfc           | Super Famicom
smc           | Super Nintendo
fig           | Super Nintendo (Pro Fighter)
swc           | Super Nintendo (Super Wild Card)
gd3 gd7       | Super Nintendo (Game Doctor)
dx2           | Super Nintendo (copier)
mgd           | Super Nintendo / Mega Drive (Multi Game Doctor)
bs bsx        | Satellaview
# Nintendo - Game Boy / GBC / GBA
gb            | Game Boy
dmg           | Game Boy / Macintosh disk image
gbc cgb       | Game Boy Color
sgb           | Super Game Boy
gbx           | Game Boy extended
gba agb       | Game Boy Advance
mb            | Game Boy Advance multiboot
# Nintendo - N64 / 64DD
n64           | Nintendo 64
z64           | N64 (Z64)
v64           | N64 (V64)
ndd           | Nintendo 64DD
# Nintendo - GameCube / Wii / Wii U
gcm           | GameCube disc
gcz           | GameCube compressed
ciso          | GameCube/Wii compact ISO
tgc           | GameCube embedded disc
wia           | Wii/GC WIA compressed
rvz           | Wii/GC compressed
wbfs          | Wii disc
wad           | Wii WAD / Doom WAD
dol           | GameCube/Wii homebrew
rpx           | Wii U executable
wux           | Wii U compressed
wud           | Wii U disc image
wua           | Wii U archive
wuhb          | Wii U homebrew bundle
tmd tik cert  | Wii U / 3DS title metadata, ticket, cert
h3            | Wii U content hash
# Nintendo - DS / DSi / 3DS
nds           | Nintendo DS
srl           | Nintendo DS (SRL)
dsi           | Nintendo DSi
3ds           | Nintendo 3DS
3dsx          | Nintendo 3DS homebrew
cci           | Nintendo 3DS
cxi           | Nintendo 3DS
ncch          | Nintendo 3DS NCCH
cia           | Nintendo 3DS CIA
z3dsx zcci zcxi | Nintendo 3DS compressed (Azahar)
axf           | Nintendo 3DS ARM executable
smdh          | Nintendo 3DS icon/metadata
# Nintendo - Switch
nsp           | Nintendo Switch
xci           | Nintendo Switch
nsz           | Nintendo Switch compressed NSP
xcz           | Nintendo Switch compressed XCI
nca ncz       | Nintendo Switch content archive
nro nso       | Nintendo Switch homebrew / executable

# Sega (SG-1000, SMS, GG, MD, 32X, CD, Pico, Saturn, DC)
sg            | Sega SG-1000
sc            | Sega SC-3000
sf7           | Sega SF-7000
sms           | Sega Master System
gg            | Sega Game Gear
md            | Sega Mega Drive
gen           | Sega Mega Drive
smd           | Sega Mega Drive (interleaved)
32x           | Sega 32X
scd           | Sega CD
pco           | Sega Pico
sat           | Sega Saturn
ss            | Sega Saturn
dc            | Dreamcast
gdi           | Dreamcast GDI
cdi           | DiscJuggler disc image (Dreamcast etc)

# Sony (PS1, PS2, PSP, PS3, PS4, Vita)
cbn           | PlayStation disc image
psexe         | PlayStation executable
pbp           | PlayStation Portable
cso           | PSP compressed ISO
zso           | PSP/PS2 compressed ISO (LZ4)
dax           | PSP DAX
prx           | PSP plugin/module
isz           | Compressed ISO (PS2 etc)
pkg           | PS3/PS4 package
rap           | PS3 PSN licence
edat          | PS3 encrypted data
self sprx     | PS3 executable
ird           | PS3 ISO rebuild data
dvd           | DVD layer descriptor (PS2/Xbox)
vpk           | Vita package
velf          | VELF homebrew
psvita        | PS Vita game

# NEC / SNK
pce           | PC Engine
sgx           | SuperGrafx
ngp           | Neo Geo Pocket
ngc ngpc npc  | Neo Geo Pocket Color
neo           | Neo Geo cartridge (NeoSD/MAME)

# Microsoft Xbox
xbe           | Original Xbox
xiso          | Xbox ISO
zar           | Xbox 360 archive

# Commodore 64 / 128 / VIC-20 / Plus4 / PET
d64           | Commodore 64 disk
d6z d7z d8z   | Commodore disk (compressed)
t64           | C64 tape container
g64 g41       | C64 disk (GCR)
g4z g6z       | C64 disk (GCR, compressed)
p64           | C64 disk (flux)
d71           | Commodore 128 disk
d81           | Commodore 128 disk
d82           | Commodore 8250 disk
d1m d2m d4m   | Commodore 1581 / CMD FD disk
x64 x6z       | Commodore disk (X64)
prg           | C64 program
p00           | C64 program
nib nbz       | C64 nibble disk
crt           | C64 cartridge
c64           | Commodore 64
vsf           | VICE snapshot
d40           | Commodore 1540
d80           | Commodore 8050
reu           | C64 REU image
tap           | ZX Spectrum / C64 tape
20 40 60 70 a0 b0 | Commodore VIC-20 cartridge
# Commodore Amiga
adf           | Amiga/BBC disk
adz           | Amiga disk (compressed)
dms           | Amiga Disk Masher
ipf           | Amiga/SPS disk
hdf           | Amiga hard disk
hdz           | Amiga hard disk (compressed)
rp9           | Amiga Forever package
lha lzh       | LHA archive (Amiga WHDLoad / Japanese PC)
lzx           | Amiga LZX archive
uae           | Amiga/UAE disk config

# Sinclair ZX Spectrum / ZX81
tzx           | ZX Spectrum tape
z80           | ZX Spectrum snapshot
sna           | ZX Spectrum snapshot
szx           | ZX Spectrum state
csw           | ZX Spectrum compressed tape
rzx           | ZX Spectrum input recording
slt           | ZX Spectrum SLT snapshot
spg           | ZX Spectrum SPG snapshot
pok           | ZX Spectrum POKEs
trd           | ZX Spectrum TR-DOS disk
scl           | ZX Spectrum TR-DOS disk (SCL)
dsk           | Multi-system floppy
mdr           | ZX Microdrive cartridge
mgt           | ZX DISCiPLE/+D
opd           | ZX Opus Discovery
sp            | ZX Spectrum SPECTRA
udi           | ZX UDI disk
nex           | ZX Spectrum Next
p 81 t81      | Sinclair ZX81
$c $b $u $z $t $w $x | ZX Microdrive

# Amstrad CPC
cdt           | Amstrad CPC tape
cpr           | Amstrad CPC cartridge

# BBC Micro / Acorn / Archimedes
ssd           | BBC Micro single-sided
dsd           | BBC Micro double-sided
uef           | BBC Micro tape
bbc           | BBC Micro
adl           | Acorn/BBC disk
adm           | Acorn/BBC disk
ads           | Acorn ADFS disk
apd           | Acorn Archimedes disk
arc           | Acorn Archimedes
ark           | Acorn RISC OS

# MSX
mx1           | MSX ROM
mx2           | MSX2 ROM
rom           | Generic ROM / MSX
ri            | MSX ROM
di1 di2       | MSX disk
fd1 fd2       | MSX disk
xsa           | MSX compressed disk

# Apple II / IIGS / Macintosh
2mg 2img      | Apple II disk image
do            | Apple II DOS-order disk
po            | Apple II ProDOS
hdv           | Apple II hard disk volume
woz           | Apple II flux (Applesauce)
a2r           | Apple II flux
moof          | Macintosh flux (Applesauce)
d13           | Apple II 13-sector
shk           | Apple II ShrinkIt archive
dc42          | Macintosh DiskCopy 4.2
hfv hvf       | Macintosh HFS volume
mac           | Macintosh disk image

# NEC PC-88 / PC-98 / Sharp X1 / X68000 / FM Towns
d77           | PC-88 floppy
d88           | PC-88 floppy
88d           | PC-88 floppy
u88           | PC-88 disk
d98 98d       | PC-98 floppy
hdi hdd nhd thd | PC-98 hard disk image
tfd dup       | PC-98 / X1 floppy
dx1           | Sharp X1 disk
cdr           | FM Towns CD image
nfd           | Japanese PC floppy
t88           | NEC PC-88 tape
p6            | NEC PC-6001
mzf           | Sharp MZ tape
mzt           | Sharp MZ tape
formzt        | Sharp MZ format
qdf           | Sharp MZ disk

# Thomson / Alice
k7            | Thomson MO/TO tape
fd            | Thomson / generic floppy
m5            | Thomson MO5
m7            | Thomson MO7
sap           | Atari 8-bit music / Thomson

# Dragon / Tandy CoCo
dgn           | Dragon 32/64
vdk           | Dragon 32/64 disk
ccc           | Tandy CoCo cartridge
pak           | Dragon / CoCo / misc pack
cgd           | Dragon CoCo

# Texas Instruments
rpk           | TI-99/4A cartridge
tifiles ticart tidisk titape | TI-99/4A (TOSEC)
73p 82p 83p 85p 86p 89p 92p 9xp 8xp | TI graphing calculator program
8xk 8xg 8xa 8xi 8xl 8xm 83g 82g 73g | TI graphing calculator app/group/data
85s 86s 83s 73k 73l 82a 82b 82i 82l 82m | TI graphing calculator misc
83a 83b 83d 83i 83l 83m 85a 85i 86a 86i 89i 92i 9xi 9xz 73i | TI graphing calculator misc

# CP/M systems
cpm           | CP/M disk
com           | CP/M executable

# Coleco / Intellivision / Vectrex / Odyssey / Channel F / misc consoles
col           | ColecoVision
cv            | ColecoVision
int intv      | Intellivision
o2            | Odyssey 2
vec           | Vectrex
chf           | Fairchild Channel F
tvc           | Emerson Arcadia 2001
myv           | Epoch My Vision
sv            | Watara Supervision
duck          | Mega Duck
vboy          | Virtual Boy
uze           | Uzebox
gam           | Vectrex / misc game
pdb prc pqa   | Palm OS
jad jar       | Java ME (J2ME)
sis sisx      | Symbian / N-Gage
n-gage ngage  | Nokia N-Gage
apk xapk obb  | Android app / expansion
ipa           | iOS app

# WonderSwan / Virtual Boy / Pokemon Mini
ws            | WonderSwan
wsc pc2       | WonderSwan Color
vb            | Virtual Boy
min           | Pokemon Mini / No-Intro minimal ROM

# Fantasy consoles / CHIP-8 / interactive fiction
arduboy       | Arduboy ROM
hex           | Intel HEX (Arduboy etc)
p8            | PICO-8 cartridge
tic           | TIC-80 cartridge
ch8 c8 sc8 xo8 | CHIP-8 / SCHIP / XO-CHIP
z1 z2 z3 z4 z5 z6 z7 z8 | Infocom Z-machine story
zblorb zlb gblorb ulx | Z-machine / Glulx story
t3            | TADS 3 story

# Sord M5 / MTX / Enterprise / SAM Coupe / Nascom / misc micros
mtx           | Memotech MTX
run           | Memotech MTX / misc
ep128s        | Enterprise 128 snapshot
dtf           | Enterprise 128
sad           | SAM Coupé disk
sdf           | SAM Coupé disk format
sbt           | SAM Coupé tape
nas           | Nascom
ace           | Jupiter Ace / Camputers Lynx
gal           | Galaksija
gtp           | Galaksija tape
bkp           | Robotron / KC85 backup
cram          | Robotron compressed RAM
kcc           | Robotron KC85 program
rk5           | Soviet Radio-86RK
rx1           | Soviet micro
rim           | Soviet/Russian micro
tu6           | Soviet micro
dai           | DAI Personal Computer
vtp           | VTech / misc
fbt           | VTech / misc
vz            | VTech Laser / VZ200
ptp           | Paper tape image
pnd           | Open Pandora package

# TRS-80 / Oric / misc 8-bit
dmk           | TRS-80 / CoCo disk
bas           | BASIC source (multi-system)
cmd           | Commodore / misc command
lbr           | CP/M library archive
wr3           | Misc 8-bit
dmp           | Memory dump
caq           | TRS-80 CoCo
cjr           | TRS-80 / misc 8-bit
bac           | TRS-80 / misc 8-bit
jrc           | Misc 8-bit
ihx           | Intel HEX
asm           | Assembly source (TOSEC)
a22           | TRS-80 / misc 8-bit
v0            | Misc 8-bit
app           | TRS-80 / misc application
asy           | Misc 8-bit
ort           | Oric tape
fpk           | Misc 8-bit pack
pss           | Misc 8-bit save state
sol           | Misc 8-bit
svt           | Misc 8-bit
smu           | Misc 8-bit
srn           | Misc 8-bit
sda           | SpectraVideo self-extract
sfx           | SpectraVideo / misc SFX
mdv           | Sinclair QL Microdrive
bee           | Aamber Pegasus / Microbee
pop           | Misc 8-bit
bs5           | Misc 8-bit
ecb           | Misc 8-bit
ent           | Misc 8-bit
sd            | Misc SD card image
dck           | Mattel Aquarius / misc
l3 l3b l3c    | Misc 8-bit
s             | Object file (TOSEC)

# Floppy disk / flux image formats (multi-system)
hfe           | HxC floppy emulator
fdi           | Floppy disk image
fsd           | BBC Micro floppy
td0           | Teledisk floppy
imd           | ImageDisk floppy
dim           | Floppy disk image
xdf           | Extended floppy
hdm           | Japanese PC floppy
2d 2hd        | Japanese PC floppy
dcp           | Disk image
hxcstream     | HxC stream flux
mfm           | MFM flux image
mfi           | MAME floppy image
86f           | 86Box floppy image
cqm cqi       | CopyQM floppy image
pdi           | Floppy flux image
pfdc          | PFDC floppy
pri           | Floppy flux image
psi           | Floppy sector image
tc            | Floppy track image
1dd           | Floppy disk image (1DD)
ima           | DOS floppy image
imz           | Compressed floppy image
cp2           | Disk image format
edd           | Apple II / floppy
jfd           | Japanese floppy disk
fdd           | Floppy disk image
baz           | Floppy disk format
wdr           | ZX disk image
scp           | SuperCard Pro flux
dfi           | DiscFerret flux image
ctr           | Atari ST / flux image
vhd           | Virtual hard disk image

# CD / DVD / disc images
iso           | Disc image (ISO 9660)
bin           | Generic binary ROM / Mega Drive
img           | Generic disc/floppy image
mdf           | Disc image (Alcohol)
mds           | Disc image descriptor
ccd           | Clone CD image
sub           | Clone CD subchannel
nrg           | Nero disc image
b5t b6t       | BlindWrite disc image
bwt           | BlindWrite disc image
ecm           | ECM compressed disc
chd           | MAME/arcade CHD

# Pinball (Visual Pinball / Future Pinball)
vpx vpt       | Visual Pinball table
directb2s     | Visual Pinball backglass
fpt           | Future Pinball table

# Emulator saves / states / BIOS / cheats
sav           | Save file
srm           | SRAM save
sra eep       | N64 SRAM / EEPROM save
mpk           | N64 Controller Pak
fla           | Flash save
dsv           | DS save (DeSmuME)
gci gcs       | GameCube save
mcd           | Memory card image
mcr           | Memory card save
ps2 vmc       | PS2 memory card
psu psv       | PlayStation save export
vmp           | PSP save
vms           | Dreamcast VMU
sta           | Save state
state         | Save state
ss1 ss2       | Save state slot
bios          | BIOS file
cht           | Cheat file

# ROM patches / hacks
ips           | ROM patch (IPS)
bps           | ROM patch (BPS)
ups           | ROM patch (UPS)
xdelta vcdiff | ROM patch (xdelta)
ppf           | Disc patch (PlayStation PPF)
aps           | ROM patch (APS)
rup           | ROM patch (NINJA RUP)
bdf           | ROM patch (BSDiff)

# Misc transport / tape
tape          | Generic tape image
t77           | Toshiba Pasopia tape
cmt           | Tape image (generic)
wav           | Audio tape capture
aif           | Audio (tape capture)
raw           | Raw dump

# Misc / uncategorised TOSEC / No-Intro
trim          | No-Intro trimmed ROM
elf           | ARM/Linux executable ROM
lib           | Library file (TOSEC)
orig          | Original (TOSEC backup)
pl            | TOSEC playlist
prn           | Printer output (TOSEC)
out           | Unix executable (TOSEC)
wmf           | Windows Metafile (TOSEC)
"""

# ════════════════════════════════════════════════════════════════════════
#  Music: chiptune / VGM / trackers / game audio rips
# ════════════════════════════════════════════════════════════════════════
MUSIC_TABLE = r"""
# VGM family
vgm           | Video Game Music
vgz           | VGM compressed
gym           | Sega Genesis/MD music
s98           | NEC PC-98 music

# SID (Commodore 64 music)
sid           | C64 SID music
psid          | C64 SID music (old format)
rsid          | C64 SID (RSID format)

# ZX Spectrum / AY chip music
ay            | ZX Spectrum AY chip music
psg           | AY / PSG register dump
stc           | ZX Spectrum STC tracker
stp           | ZX Spectrum STP tracker
sqt           | ZX Spectrum SQ-Tracker
pt1           | ZX ProTracker 1
pt2           | ZX ProTracker 2
pt3           | ZX ProTracker 3
psc           | ZX Spectrum PROTRACKER

# Amiga / PC tracker music
mod           | Amiga ProTracker module
xm            | FastTracker II module
s3m           | ScreamTracker 3
it            | Impulse Tracker music
mptm          | OpenMPT module
med           | Amiga MED/OctaMED
oct okt       | Amiga OctaMED / Oktalyzer
dbm           | Amiga DigiBooster
ahx           | Amiga AHX tracker
hvl           | Amiga HivelyTracker
stm           | ScreamTracker module
669           | Composer 669 tracker
far           | Farandole tracker
mtm           | MultiTracker module
ult           | UltraTracker module
ptm           | PolyTracker module
amf           | DSMI / Asylum module
gdm           | General DigiMusic module
imf           | Imago Orpheus / id Software music
mdl           | DigiTrakker module
psm           | Epic MegaGames MASI module
umx           | Unreal music package
mo3           | MO3 compressed module
dmf           | Deflemask tracker
tfm tfe       | TFM tracker
fc13 fc14     | Future Composer

# Nintendo music rips
nsf           | NES music rip
nsfe          | NES music (extended)
gbs           | Game Boy music rip
hes           | PC Engine music rip
spc           | SNES SPC700 music
rsn           | SNES music archive
snsf minisnsf | SNES music rip (SNSF)
usf miniusf   | N64 music rip
gsf minigsf   | GBA music rip
2sf mini2sf   | NDS music rip
ncsf minincsf | NDS music rip (NCSF)

# Sega / Sony / other rip formats
kss           | MSX/Sega music
psf minipsf   | PlayStation music rip
psf2 minipsf2 | PS2 music rip
ssf minissf   | Saturn music rip
dsf minidsf   | Dreamcast music rip
qsf miniqsf   | Capcom QSound music rip

# Atari music
sndh          | Atari ST SNDH music
sc68          | Atari ST SC68 music
ym            | Atari ST YM chip music
rmt           | Atari 8-bit Raster Music Tracker
cmc           | Atari 8-bit Chaos Music Composer

# AdLib / OPL / Japanese computer chip music
hsc           | AdLib HSC music
ksm           | AdLib KSM music
lds           | AdLib LDS music
rad           | Reality AdLib Tracker
mdx pdx       | Sharp X68000 MDX music / samples
mgs           | MSX MGSDRV music

# CPC music
cpc           | Amstrad CPC / CPC music

# MIDI / sound fonts
mid midi rmi  | MIDI music
xmi hmp hmi   | Game MIDI (Miles / HMI)
sf2 sbk dls   | Sound font / instrument bank

# Game audio rips (vgmstream, VGM Archive DATs)
adx ahx       | CRI ADX / AHX audio
hca           | CRI HCA audio
acb awb       | CRI ADX2 cue/wave bank
aix           | CRI AIX audio
fsb fev       | FMOD sound bank / event
bnk           | Wwise / game sound bank
wem           | Wwise audio
xwb xsb xgs   | XACT wave/sound bank
xma           | Xbox XMA audio
at3 at9 aa3   | Sony ATRAC audio
vag xvag      | Sony VAG audio
msf           | PS3 MSF audio
sgd           | Sony SGD audio
xa            | PlayStation XA audio
brstm bcstm bfstm | Nintendo stream audio
bwav bcwav bfwav | Nintendo wave audio
dsp idsp      | Nintendo DSP ADPCM audio
ast           | Nintendo AST audio
nus3bank      | Nintendo/Namco NUS3 sound bank
rsd sab sps   | Game audio stream
adp adpcm     | ADPCM game audio
lwav logg laac lmp3 | vgmstream looped audio
txtp txth genh vgmstream | vgmstream metadata / header
cdg           | Karaoke CD+G graphics
"""

# ════════════════════════════════════════════════════════════════════════
#  Media: artwork, video, audio, manuals / documents
#  (moved into the ROM output alongside sets, like companion files)
# ════════════════════════════════════════════════════════════════════════
MEDIA_TABLE = r"""
# Artwork / images
png           | Image (PNG)
jpg jpeg jpe jfif | Image (JPEG)
gif           | Image (GIF)
bmp           | Image (BMP)
tif tiff      | Image (TIFF)
webp          | Image (WebP)
avif heic jxl jp2 | Image (modern)
tga           | Image (Targa)
psd xcf       | Image (Photoshop / GIMP)
pcx           | Image (PCX)
iff ilbm lbm  | Image (Amiga IFF)
ico icns      | Icon
svg           | Image (vector)
dds           | Image (DirectDraw texture)
ppm pgm pbm   | Image (Netpbm)

# Video
mp4 m4v       | Video (MPEG-4)
mkv           | Video (Matroska)
avi divx xvid | Video (AVI)
mov           | Video (QuickTime)
wmv asf       | Video (Windows Media)
mpg mpeg mpe m1v m2v | Video (MPEG)
vob           | Video (DVD)
ts m2ts mts   | Video (MPEG transport stream)
webm          | Video (WebM)
flv f4v       | Video (Flash)
3gp 3g2       | Video (3GP)
ogv ogm       | Video (Ogg)
rm rmvb       | Video (RealMedia)
bik bk2       | Video (Bink)
smk           | Video (Smacker)
usm           | Video (CRI Sofdec)
thp           | Video (GameCube THP)
roq           | Video (RoQ)
mve           | Video (Interplay MVE)
dxa           | Video (ScummVM DXA)
swf           | Flash animation / game

# Audio
mp3 mp2       | Audio (MPEG)
ogg oga opus  | Audio (Ogg / Opus)
flac          | Audio (FLAC)
aiff aifc     | Audio (AIFF)
m4a aac       | Audio (AAC)
ac3 dts       | Audio (Dolby / DTS)
wma           | Audio (Windows Media)
wv            | Audio (WavPack)
ape           | Audio (Monkey's Audio)
mpc           | Audio (Musepack)
tta tak shn   | Audio (lossless)
mka           | Audio (Matroska)
au            | Audio (Sun AU)
voc           | Audio (Creative Voice / CPC tape)
8svx          | Audio (Amiga 8SVX)
ra            | Audio (RealAudio)
caf           | Audio (Core Audio)

# Manuals / documents / scans / books
pdf           | Document (PDF)
doc docx      | Document (Word)
odt           | Document (OpenDocument)
rtf           | Document (rich text)
wri           | Document (Windows Write)
htm html      | Document (HTML)
mht mhtml     | Document (web archive)
chm           | Help file (CHM)
hlp           | Help file (Windows Help)
xps oxps      | Document (XPS)
epub mobi azw3 | eBook
djvu djv      | Document scan (DjVu)
cbz cbr cb7   | Comic / scanned book archive
"""

# ════════════════════════════════════════════════════════════════════════
#  Companion files: cue sheets, playlists, hashes, release info
# ════════════════════════════════════════════════════════════════════════
COMPANION_TABLE = r"""
# Cue sheets / playlists / descriptors
cue           | Disc cue sheet
toc           | Disc TOC (cdrdao)
sbi           | PlayStation subchannel (Redump)
m3u m3u8      | Multi-disc playlist
xml           | XML metadata / gamelist
dat           | DAT file / data

# Hashes / release info
md5 sha1 sha256 crc | Checksum file
sfv           | Checksum file (SFV)
nfo           | Release info
diz           | Release info (file_id.diz)
txt           | Text / readme
srr srs       | ReScene archive / sample data
par2          | Parity recovery
"""


def _parse(table: str) -> dict:
    """Parse a table into {ext: (group, description)}."""
    out: dict = {}
    group = ""
    for line in table.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            group = line.lstrip("#").strip()
            continue
        exts, _, desc = line.partition("|")
        for e in exts.split():
            out.setdefault(e.lower().lstrip("."), (group, desc.strip()))
    return out


ROM = _parse(ROM_TABLE)
MUSIC = _parse(MUSIC_TABLE)
MEDIA = _parse(MEDIA_TABLE)
COMPANION = _parse(COMPANION_TABLE)

# A ROM entry wins over the same ext elsewhere (e.g. .wav, .aif tape captures)
MUSIC = {e: v for e, v in MUSIC.items() if e not in ROM}
MEDIA = {e: v for e, v in MEDIA.items() if e not in ROM and e not in MUSIC}
COMPANION = {e: v for e, v in COMPANION.items()
             if e not in ROM and e not in MUSIC and e not in MEDIA}

# What the File Sorter uses
ROM_EXTS = frozenset(ROM) | frozenset(MUSIC)           # -> ROM output (ROM)
COMPANION_EXTS = frozenset(COMPANION) | frozenset(MEDIA)  # -> ROM output (COMP)
KNOWN_EXTS = ROM_EXTS | COMPANION_EXTS

# MAME / arcade chip dumps have free-form extensions (.6e .u12 .ic3 .p1)
# and split archives use numbered parts (.001 .r00 .z01).  None of those can
# be listed, so "known only" mode can keep anything shaped like them.
_CHIP_RE = re.compile(r"^(?=[a-z0-9]*\d)[a-z0-9]{1,4}$")


def is_chip_like(ext: str) -> bool:
    """True for short extensions with a digit: .6e .u12 .ic3 .001 .r00."""
    return bool(_CHIP_RE.match(ext.lower()))


def describe(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    for table in (ROM, MUSIC, MEDIA, COMPANION):
        if ext in table:
            return table[ext][1]
    return ""


def write_reference(path) -> int:
    """Write the human-readable ROM_Extensions_Reference.txt; returns count."""
    sections = [
        ("ROM / DISK / TAPE / DISC IMAGES, SAVES, PATCHES", ROM),
        ("MUSIC / CHIPTUNE / VGM / GAME AUDIO RIPS", MUSIC),
        ("MEDIA / ARTWORK / VIDEO / AUDIO / MANUALS", MEDIA),
        ("COMPANION FILES (cue sheets, hashes, release info)", COMPANION),
    ]
    rule = "=" * 70
    total = sum(len(t) for _, t in sections)
    lines = [
        rule,
        "  RetroVault Toolkit - Recognised ROM / Music / Media File Extensions",
        "  Generated from rom_extensions.py -- edit THAT file, then run",
        "      python rom_extensions.py",
        "  to regenerate this reference.",
        rule,
        f"  Total: {total} extensions"
        f"  (ROM {len(ROM)}, music {len(MUSIC)},"
        f" media {len(MEDIA)}, companion {len(COMPANION)})",
        "",
        "  ROM + music   -> ROM output (counted as ROM)",
        "  media + companion -> ROM output when 'companion & media' is on",
        "  anything else -> General output, or DELETED in 'known only' mode",
        "",
    ]
    for title, table in sections:
        lines += ["-" * 70, f"  {title}  ({len(table)})", "-" * 70]
        group = None
        for ext, (grp, desc) in table.items():
            if grp != group:
                group = grp
                lines += ["", f"  [{grp}]"]
            lines.append(f"  .{ext:<15} {desc}")
        lines.append("")
    lines += [rule, "  ALPHABETICAL INDEX", rule]
    for ext in sorted(KNOWN_EXTS):
        lines.append(f"  .{ext:<15} {describe(ext)}")
    lines += ["", rule]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return total


if __name__ == "__main__":
    ref = Path(__file__).with_name("ROM_Extensions_Reference.txt")
    n = write_reference(ref)
    print(f"Wrote {ref.name}: {n} extensions")
