#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PKSM_REF = os.environ.get("PKSM_CORE_REF", "master")
MEMECRYPTO_REF = os.environ.get("MEMECRYPTO_REF", "master")
PCG_CPP_REF = os.environ.get("PCG_CPP_REF", "master")
PKHOUSE_REF = os.environ.get("PKHOUSE_REF", "main")

GENERATED_FILES = {'common/include/clone_protocol.hpp': "#pragma once\n#include <cstddef>\n#include <cstdint>\n#include <cstring>\n\nnamespace cloneproto {\nconstexpr std::uint16_t VERSION = 1;\nconstexpr std::uint16_t TCP_PORT = 5000;\nconstexpr std::size_t PK7_SIZE = 232;\nconstexpr std::size_t HDR_SIZE = 8;\nconstexpr std::uint8_t MAGIC[4] = {'P','K','7','C'};\n\ninline void buildHeader(std::uint8_t out[HDR_SIZE]) {\n    out[0]=MAGIC[0]; out[1]=MAGIC[1]; out[2]=MAGIC[2]; out[3]=MAGIC[3];\n    out[4]=static_cast<std::uint8_t>((VERSION>>8)&0xFF);\n    out[5]=static_cast<std::uint8_t>(VERSION&0xFF);\n    out[6]=static_cast<std::uint8_t>((PK7_SIZE>>8)&0xFF);\n    out[7]=static_cast<std::uint8_t>(PK7_SIZE&0xFF);\n}\n\ninline bool validateHeader(const std::uint8_t in[HDR_SIZE]) {\n    if (std::memcmp(in,MAGIC,sizeof(MAGIC))!=0) return false;\n    const std::uint16_t version=(static_cast<std::uint16_t>(in[4])<<8)|in[5];\n    const std::uint16_t size=(static_cast<std::uint16_t>(in[6])<<8)|in[7];\n    return version==VERSION && size==PK7_SIZE;\n}\n}\n", '3ds/source/main.cpp': '#include <3ds.h>\n#include <arpa/inet.h>\n#include <errno.h>\n#include <malloc.h>\n#include <netinet/in.h>\n#include <sys/socket.h>\n#include <unistd.h>\n#include <algorithm>\n#include <array>\n#include <cstdio>\n#include <memory>\n\n#include "clone_protocol.hpp"\n#include "pkx/PKX.hpp"\n#include "sav/Sav7.hpp"\n#include "sav/SavSUMO.hpp"\n#include "sav/SavUSUM.hpp"\n\nnamespace {\nconstexpr std::size_t SOC_BUFFER_SIZE=0x100000;\nconstexpr std::size_t SUMO_SAVE_SIZE=0x6BE00;\nconstexpr std::size_t USUM_SAVE_SIZE=0x6CC00;\nu32* g_socBuffer=nullptr;\n\nstruct SaveImage { std::shared_ptr<u8[]> bytes; std::size_t size=0; };\n\nbool sendAll(int fd,const void* data,std::size_t size) {\n    const auto* p=static_cast<const std::uint8_t*>(data);\n    while(size) {\n        const ssize_t n=::send(fd,p,size,0);\n        if(n<0) { if(errno==EINTR) continue; return false; }\n        if(n==0) return false;\n        p+=n; size-=static_cast<std::size_t>(n);\n    }\n    return true;\n}\n\nbool readCurrentTitleSaveMain(SaveImage& out) {\n    FS_Archive archive{}; Handle file=0;\n    Result rc=FSUSER_OpenArchive(&archive,ARCHIVE_SAVEDATA,fsMakePath(PATH_EMPTY,""));\n    if(R_FAILED(rc)) { std::printf("OpenArchive failed: 0x%08lX\\n",(unsigned long)rc); return false; }\n    rc=FSUSER_OpenFile(&file,archive,fsMakePath(PATH_ASCII,"/main"),FS_OPEN_READ,0);\n    if(R_FAILED(rc)) { std::printf("OpenFile failed: 0x%08lX\\n",(unsigned long)rc); FSUSER_CloseArchive(archive); return false; }\n    u64 fileSize=0; rc=FSFILE_GetSize(file,&fileSize);\n    if(R_FAILED(rc) || (fileSize!=SUMO_SAVE_SIZE && fileSize!=USUM_SAVE_SIZE)) {\n        std::printf("Unsupported save size: 0x%llX\\n",(unsigned long long)fileSize);\n        FSFILE_Close(file); FSUSER_CloseArchive(archive); return false;\n    }\n    auto bytes=std::shared_ptr<u8[]>(new u8[(std::size_t)fileSize],std::default_delete<u8[]>());\n    u32 bytesRead=0; rc=FSFILE_Read(file,&bytesRead,0,bytes.get(),(u32)fileSize);\n    FSFILE_Close(file); FSUSER_CloseArchive(archive);\n    if(R_FAILED(rc) || bytesRead!=fileSize) return false;\n    out.bytes=std::move(bytes); out.size=(std::size_t)fileSize; return true;\n}\n\nbool extractPlainPk7(const SaveImage& image,int box,int slot,\n                     std::array<std::uint8_t,cloneproto::PK7_SIZE>& output) {\n    std::unique_ptr<pksm::Sav7> save;\n    if(image.size==SUMO_SAVE_SIZE) save=std::make_unique<pksm::SavSUMO>(image.bytes);\n    else if(image.size==USUM_SAVE_SIZE) save=std::make_unique<pksm::SavUSUM>(image.bytes);\n    else return false;\n    auto pkm=save->pkm((u8)box,(u8)slot);\n    if(!pkm || pkm->getLength()<cloneproto::PK7_SIZE) return false;\n    if(pkm->isEncrypted()) pkm->decrypt();\n    const auto raw=pkm->rawData();\n    std::copy_n(raw.begin(),cloneproto::PK7_SIZE,output.begin());\n    return true;\n}\n\nint createServer() {\n    const int fd=::socket(AF_INET,SOCK_STREAM,0); if(fd<0) return -1;\n    int yes=1; setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));\n    sockaddr_in addr{}; addr.sin_family=AF_INET; addr.sin_port=htons(cloneproto::TCP_PORT); addr.sin_addr.s_addr=htonl(INADDR_ANY);\n    if(::bind(fd,(sockaddr*)&addr,sizeof(addr))<0 || ::listen(fd,2)<0) { ::close(fd); return -1; }\n    return fd;\n}\n\nvoid printIp() {\n    const u32 ip=gethostid();\n    std::printf("3DS IP: %lu.%lu.%lu.%lu\\n",(unsigned long)(ip&0xFF),(unsigned long)((ip>>8)&0xFF),(unsigned long)((ip>>16)&0xFF),(unsigned long)((ip>>24)&0xFF));\n}\n\nbool servePk7(int server,const std::array<std::uint8_t,cloneproto::PK7_SIZE>& pk7) {\n    std::printf("Waiting for Switch...\\n");\n    sockaddr_in peer{}; socklen_t len=sizeof(peer);\n    const int client=::accept(server,(sockaddr*)&peer,&len); if(client<0) return false;\n    std::uint8_t hdr[cloneproto::HDR_SIZE]; cloneproto::buildHeader(hdr);\n    const bool ok=sendAll(client,hdr,sizeof(hdr)) && sendAll(client,pk7.data(),pk7.size());\n    ::shutdown(client,SHUT_RDWR); ::close(client); return ok;\n}\n}\n\nint main() {\n    gfxInitDefault(); consoleInit(GFX_TOP,nullptr);\n    Result rc=fsInit(); if(R_FAILED(rc)) { gfxExit(); return 1; }\n    g_socBuffer=(u32*)memalign(0x1000,SOC_BUFFER_SIZE); if(!g_socBuffer) { fsExit(); gfxExit(); return 1; }\n    rc=socInit(g_socBuffer,SOC_BUFFER_SIZE); if(R_FAILED(rc)) { free(g_socBuffer); fsExit(); gfxExit(); return 1; }\n\n    std::printf("Pokemon PK7 Copy Server\\n=======================\\n\\nSOURCE ACCESS: READ ONLY\\n\\n");\n    SaveImage save;\n    if(!readCurrentTitleSaveMain(save)) {\n        std::printf("Could not load Gen-7 /main.\\nSTART = exit\\n");\n        while(aptMainLoop()) { hidScanInput(); if(hidKeysDown()&KEY_START) break; }\n        socExit(); free(g_socBuffer); fsExit(); gfxExit(); return 1;\n    }\n    std::printf("Detected: %s\\n",save.size==SUMO_SAVE_SIZE?"Sun / Moon":"Ultra Sun / Ultra Moon");\n    const int server=createServer(); if(server<0) { socExit(); free(g_socBuffer); fsExit(); gfxExit(); return 1; }\n    printIp(); std::printf("TCP port: %u\\n\\n",cloneproto::TCP_PORT);\n\n    int box=0,slot=0;\n    while(aptMainLoop()) {\n        std::printf("\\rBox %02d / Slot %02d    ",box+1,slot+1);\n        gfxFlushBuffers(); gfxSwapBuffers(); gspWaitForVBlank(); hidScanInput();\n        const u32 down=hidKeysDown();\n        if(down&KEY_START) break;\n        if(down&KEY_UP) box=(box+31)%32; if(down&KEY_DOWN) box=(box+1)%32;\n        if(down&KEY_LEFT) slot=(slot+29)%30; if(down&KEY_RIGHT) slot=(slot+1)%30;\n        if(down&KEY_A) {\n            std::array<std::uint8_t,cloneproto::PK7_SIZE> pk7{};\n            if(!extractPlainPk7(save,box,slot,pk7)) { std::printf("\\nCould not extract PK7.\\n"); continue; }\n            std::printf("\\nSending COPY of Box %d Slot %d...\\n",box+1,slot+1);\n            std::printf(servePk7(server,pk7)?"Transfer complete. Source unchanged.\\n":"Transfer failed.\\n");\n        }\n    }\n    ::close(server); socExit(); free(g_socBuffer); fsExit(); gfxExit(); return 0;\n}\n', 'switch/source/main.cpp': '#include <switch.h>\n#include <arpa/inet.h>\n#include <errno.h>\n#include <netinet/in.h>\n#include <sys/socket.h>\n#include <unistd.h>\n#include <array>\n#include <cstdint>\n#include <cstdio>\n#include <cstring>\n#include <iterator>\n#include <string>\n\n#include "clone_protocol.hpp"\n#include "game_type.h"\n#include "personal_bdsp.h"\n#include "personal_sv.h"\n#include "personal_swsh.h"\n#include "pokemon.h"\n#include "save_file.h"\n#include "species_converter.h"\n#include "generated_name_tables.hpp"\n\nnamespace {\n\nPadState g_pad;\n\nstd::string uiSpeciesName(std::uint16_t id);\nstd::string uiMoveName(std::uint16_t id);\nstd::string uiNatureName(std::uint8_t id);\nstd::string uiAbilityName(std::uint16_t id);\nstd::string uiItemName(std::uint16_t id);\n\nstruct NeutralMon {\n    std::uint32_t ec = 0;\n    std::uint32_t experience = 0;\n    std::uint32_t pid = 0;\n    std::uint32_t iv32 = 0;\n\n    std::uint16_t species = 0;\n    std::uint16_t tid = 0;\n    std::uint16_t sid = 0;\n\n    std::uint8_t nature = 0;\n    std::uint8_t gender = 0;\n    std::uint8_t form = 0;\n    std::uint8_t ball = 0;\n    std::uint8_t language = 0;\n    std::uint8_t originVersion = 0;\n    std::uint8_t level = 0;\n\n    bool fateful = false;\n    bool sourceIsLGPE = false;\n    bool sourceShiny = false;\n\n    std::uint16_t sourceAbility = 0;\n    std::uint16_t sourceHeldItem = 0;\n\n    std::string sourceDisplayName;\n    std::string sourceSpeciesName;\n    std::string sourceNickname;\n    std::string sourceOT;\n\n    std::array<std::uint8_t, 6> ev{};\n    std::array<std::uint16_t, 4> moves{};\n    std::array<std::uint16_t, 4> relearnMoves{};\n    std::array<std::uint8_t, 4> movePP{};\n    std::array<std::uint8_t, 4> movePPUps{};\n\n    std::array<std::uint8_t, 26> nickname{};\n    std::array<std::uint8_t, 26> htName{};\n    std::array<std::uint8_t, 26> otName{};\n};\n\nstruct BoxSlot {\n    int box = 0;\n    int slot = 0;\n};\n\nstd::uint16_t readU16(const std::uint8_t* d, std::size_t o)\n{\n    std::uint16_t v{};\n    std::memcpy(&v, d + o, 2);\n    return v;\n}\n\nstd::uint32_t readU32(const std::uint8_t* d, std::size_t o)\n{\n    std::uint32_t v{};\n    std::memcpy(&v, d + o, 4);\n    return v;\n}\n\nvoid copyUtf16Field(\n    const std::array<std::uint8_t, 26>& src,\n    Pokemon& dst,\n    int offset)\n{\n    if (offset >= 0)\n        std::memcpy(dst.data.data() + offset, src.data(), src.size());\n}\n\nstd::uint8_t gameVersionValue(GameType g)\n{\n    // PKHeX GameVersion values stored in PKM data.\n    switch (g) {\n        case GameType::GP: return 42;\n        case GameType::GE: return 43;\n        case GameType::Sw: return 44;\n        case GameType::Sh: return 45;\n        case GameType::BD: return 48;\n        case GameType::SP: return 49;\n        case GameType::S:  return 50;\n        case GameType::V:  return 51;\n        default: return 0;\n    }\n}\n\nbool isSupportedSwitchGame(GameType g)\n{\n    return isLGPE(g) || isSwSh(g) || isBDSP(g) || isSV(g);\n}\n\nbool lgpeFormSupported(std::uint16_t species, std::uint8_t form)\n{\n    if (form == 0)\n        return true;\n\n    // Conservative list of permanent Alolan forms supported by LGPE.\n    if (form != 1)\n        return false;\n\n    switch (species) {\n        case 19:  // Rattata\n        case 20:  // Raticate\n        case 26:  // Raichu\n        case 27:  // Sandshrew\n        case 28:  // Sandslash\n        case 37:  // Vulpix\n        case 38:  // Ninetales\n        case 50:  // Diglett\n        case 51:  // Dugtrio\n        case 52:  // Meowth\n        case 53:  // Persian\n        case 74:  // Geodude\n        case 75:  // Graveler\n        case 76:  // Golem\n        case 88:  // Grimer\n        case 89:  // Muk\n        case 103: // Exeggutor\n        case 105: // Marowak\n            return true;\n        default:\n            return false;\n    }\n}\n\nbool lgpeSpeciesSupported(std::uint16_t species, std::uint8_t form)\n{\n    const bool speciesOk =\n        (species >= 1 && species <= 151) ||\n        species == 808 || // Meltan\n        species == 809;   // Melmetal\n\n    return speciesOk && lgpeFormSupported(species, form);\n}\n\nbool targetSupportsPokemon(\n    GameType g,\n    std::uint16_t species,\n    std::uint8_t form,\n    std::string& reason)\n{\n    if (!isSupportedSwitchGame(g)) {\n        reason = "Unsupported destination game.";\n        return false;\n    }\n\n    if (species == 0) {\n        reason = "Source slot is empty.";\n        return false;\n    }\n\n    if (isLGPE(g)) {\n        if (!lgpeSpeciesSupported(species, form)) {\n            reason =\n                "This species/form does not exist in Let\'s Go Pikachu/Eevee.";\n            return false;\n        }\n        return true;\n    }\n\n    if (isBDSP(g)) {\n        if (species > 493) {\n            reason =\n                "BDSP only supports National Dex species 001-493.";\n            return false;\n        }\n\n        if (species >= PERSONAL_BDSP_COUNT) {\n            reason = "Species is outside the BDSP personal table.";\n            return false;\n        }\n\n        if (form > 0 && PersonalBDSP::FORM_COUNT[species] <= form) {\n            reason = "This form is not supported by BDSP.";\n            return false;\n        }\n\n        if (PersonalBDSP::getAbility(species, form, 0) == 0) {\n            reason = "BDSP personal data does not support this Pokemon.";\n            return false;\n        }\n\n        return true;\n    }\n\n    if (isSwSh(g)) {\n        if (species >= PersonalSWSH::NUM_ENTRIES) {\n            reason = "Species is outside the Sword/Shield personal table.";\n            return false;\n        }\n\n        if (!PersonalSWSH::IS_PRESENT[species]) {\n            reason = "This species is not present in Sword/Shield.";\n            return false;\n        }\n\n        if (form > 0 && PersonalSWSH::FORM_COUNT[species] <= form) {\n            reason = "This form is not supported by Sword/Shield.";\n            return false;\n        }\n\n        return true;\n    }\n\n    if (isSV(g)) {\n        const std::uint16_t internal =\n            SpeciesConverter::getInternal9(species);\n\n        if (internal == 0) {\n            reason = "This species is not present in Scarlet/Violet.";\n            return false;\n        }\n\n        if (species >= PERSONAL_SV_COUNT) {\n            reason = "Species is outside the Scarlet/Violet personal table.";\n            return false;\n        }\n\n        if (form > 0) {\n            const int formIndex =\n                PersonalSV::getEntryIndex(species, form);\n\n            if (PersonalSV::FORM_STATS_INDEX[species] == 0 ||\n                formIndex == species ||\n                formIndex >= PERSONAL_SV_COUNT ||\n                !PersonalSV::IS_PRESENT[formIndex]) {\n\n                reason = "This form is not supported by Scarlet/Violet.";\n                return false;\n            }\n        }\n\n        return true;\n    }\n\n    reason = "Unsupported conversion.";\n    return false;\n}\n\nbool neutralFromPlainPk7(\n    const std::array<std::uint8_t, cloneproto::PK7_SIZE>& pk7,\n    NeutralMon& n)\n{\n    const auto* d = pk7.data();\n\n    n.ec = readU32(d, 0x00);\n    n.species = readU16(d, 0x08);\n\n    if (!n.species)\n        return false;\n\n    n.tid = readU16(d, 0x0C);\n    n.sid = readU16(d, 0x0E);\n    n.experience = readU32(d, 0x10);\n    n.pid = readU32(d, 0x18);\n    n.nature = d[0x1C];\n\n    const std::uint8_t flags = d[0x1D];\n    n.fateful = (flags & 1) != 0;\n    n.gender = (flags >> 1) & 3;\n    n.form = flags >> 3;\n\n    for (int i = 0; i < 6; ++i)\n        n.ev[i] = d[0x1E + i];\n\n    std::memcpy(n.nickname.data(), d + 0x40, n.nickname.size());\n\n    for (int i = 0; i < 4; ++i)\n        n.moves[i] = readU16(d, 0x5A + i * 2);\n\n    for (int i = 0; i < 4; ++i)\n        n.movePP[i] = d[0x62 + i];\n\n    for (int i = 0; i < 4; ++i)\n        n.movePPUps[i] = d[0x66 + i];\n\n    for (int i = 0; i < 4; ++i)\n        n.relearnMoves[i] = readU16(d, 0x6A + i * 2);\n\n    n.iv32 = readU32(d, 0x74);\n\n    std::memcpy(n.htName.data(), d + 0x78, n.htName.size());\n    std::memcpy(n.otName.data(), d + 0xB0, n.otName.size());\n\n    n.ball = d[0xDC];\n    n.originVersion = d[0xDF];\n    n.language = d[0xE3];\n\n    n.sourceIsLGPE = false;\n    n.level = 0; // PK7 box data has no party level byte.\n\n    return true;\n}\n\nbool neutralFromPokemon(const Pokemon& src, NeutralMon& n)\n{\n    if (src.isEmpty())\n        return false;\n\n    n.ec = src.encryptionConstant();\n    n.species = src.species();\n    n.tid = src.tid();\n    n.sid = src.sid();\n    n.experience = src.readU32(0x10);\n    n.pid = src.pid();\n    n.nature = src.nature();\n    n.gender = src.gender();\n    n.form = src.form();\n    n.ball = src.ball();\n    n.language = src.language();\n    n.fateful = src.fatefulEncounter();\n    n.iv32 = src.iv32();\n    n.level = src.level();\n    n.sourceShiny = src.isShiny();\n    n.sourceAbility = src.ability();\n    n.sourceHeldItem = src.heldItem();\n    n.sourceDisplayName =\n        src.isNicknamed()\n            ? src.nickname()\n            : uiSpeciesName(src.species());\n    n.sourceSpeciesName = uiSpeciesName(src.species());\n    n.sourceNickname =\n        src.isNicknamed() ? src.nickname() : std::string();\n    n.sourceOT = src.otName();\n\n    n.moves = {\n        src.move1(),\n        src.move2(),\n        src.move3(),\n        src.move4()\n    };\n\n    const int m = src.ofs().moveBase;\n\n    for (int i = 0; i < 4; ++i) {\n        n.movePP[i] = src.data[m + 8 + i];\n        n.movePPUps[i] = src.data[m + 12 + i];\n        n.relearnMoves[i] = src.readU16(m + 16 + i * 2);\n    }\n\n    if (src.ofs().nickname >= 0)\n        std::memcpy(\n            n.nickname.data(),\n            src.data.data() + src.ofs().nickname,\n            n.nickname.size());\n\n    if (src.ofs().htName >= 0)\n        std::memcpy(\n            n.htName.data(),\n            src.data.data() + src.ofs().htName,\n            n.htName.size());\n\n    if (src.ofs().otName >= 0)\n        std::memcpy(\n            n.otName.data(),\n            src.data.data() + src.ofs().otName,\n            n.otName.size());\n\n    if (isLGPE(src.gameType_)) {\n        n.originVersion = src.data[0xDF];\n\n        // LGPE AV mechanics are not ordinary EVs.\n        n.ev.fill(0);\n        n.sourceIsLGPE = true;\n    }\n    else {\n        n.ev = {\n            src.evHp(),\n            src.evAtk(),\n            src.evDef(),\n            src.evSpe(),\n            src.evSpA(),\n            src.evSpD()\n        };\n\n        if (isSV(src.gameType_))\n            n.originVersion = src.data[0xCE];\n        else\n            n.originVersion = src.data[0xDE];\n    }\n\n    return true;\n}\n\nstd::uint16_t defaultAbility(\n    GameType g,\n    std::uint16_t species,\n    std::uint8_t form)\n{\n    if (isLGPE(g))\n        return 0; // LGPE does not use abilities in gameplay.\n\n    if (isBDSP(g)) {\n        if (species >= PERSONAL_BDSP_COUNT)\n            return 0;\n        return PersonalBDSP::getAbility(species, form, 0);\n    }\n\n    if (isSwSh(g)) {\n        if (species >= PersonalSWSH::NUM_ENTRIES)\n            return 0;\n        return PersonalSWSH::getAbility(species, form, 0);\n    }\n\n    if (isSV(g)) {\n        if (species >= PERSONAL_SV_COUNT)\n            return 0;\n        return PersonalSV::getAbility(species, form, 0);\n    }\n\n    return 0;\n}\n\nstd::uint8_t targetGrowthRate(\n    GameType g,\n    std::uint16_t species,\n    std::uint8_t form)\n{\n    if (isBDSP(g))\n        return PersonalBDSP::getGrowthRate(species, form);\n\n    if (isSwSh(g))\n        return PersonalSWSH::getGrowthRate(species, form);\n\n    if (isSV(g))\n        return PersonalSV::getGrowthRate(species, form);\n\n    return 0;\n}\n\nstd::uint8_t levelFromExperience(\n    std::uint32_t exp,\n    std::uint8_t growth)\n{\n    if (growth > 5)\n        growth = 0;\n\n    const std::uint32_t* table = EXP_TABLE[growth];\n\n    if (exp >= table[99])\n        return 100;\n\n    std::uint8_t level = 1;\n\n    while (level < 100 && exp >= table[level])\n        ++level;\n\n    return level;\n}\n\nstd::uint16_t targetSpecies(\n    GameType g,\n    std::uint16_t species)\n{\n    if (isSV(g))\n        return SpeciesConverter::getInternal9(species);\n\n    if (isLGPE(g) || isSwSh(g) || isBDSP(g))\n        return species;\n\n    return 0;\n}\n\nvoid setModernFlags(\n    Pokemon& p,\n    const NeutralMon& n)\n{\n    const auto& o = p.ofs();\n\n    if (o.fateful >= 0) {\n        const std::uint8_t mask =\n            static_cast<std::uint8_t>(1u << o.fatefulBit);\n\n        p.data[o.fateful] &= static_cast<std::uint8_t>(~mask);\n\n        if (n.fateful)\n            p.data[o.fateful] |= mask;\n    }\n\n    if (o.genderByte >= 0) {\n        const std::uint8_t mask =\n            static_cast<std::uint8_t>(3u << o.genderShift);\n\n        p.data[o.genderByte] &=\n            static_cast<std::uint8_t>(~mask);\n\n        p.data[o.genderByte] |=\n            static_cast<std::uint8_t>(\n                (n.gender & 3u) << o.genderShift);\n    }\n\n    if (o.form >= 0) {\n        if (o.formShift == 0) {\n            p.data[o.form] = n.form;\n        }\n        else {\n            const std::uint8_t mask =\n                static_cast<std::uint8_t>(\n                    0xFFu << o.formShift);\n\n            p.data[o.form] &=\n                static_cast<std::uint8_t>(~mask);\n\n            p.data[o.form] |=\n                static_cast<std::uint8_t>(\n                    n.form << o.formShift);\n        }\n    }\n}\n\nbool makeTargetPokemon(\n    const NeutralMon& n,\n    GameType g,\n    Pokemon& out,\n    std::string& reason)\n{\n    if (!targetSupportsPokemon(g, n.species, n.form, reason))\n        return false;\n\n    const std::uint16_t species =\n        targetSpecies(g, n.species);\n\n    if (species == 0) {\n        reason = "Could not map the species into the target format.";\n        return false;\n    }\n\n    const std::uint16_t ability =\n        defaultAbility(g, n.species, n.form);\n\n    if (!isLGPE(g) && ability == 0) {\n        reason = "No valid target-game ability could be assigned.";\n        return false;\n    }\n\n    Pokemon p{};\n    p.gameType_ = g;\n\n    p.writeU32(0x00, n.ec);\n    p.writeU16(p.ofs().speciesInternal, species);\n\n    // Conservative cross-game policy: no held item.\n    p.writeU16(p.ofs().heldItem, 0);\n\n    p.writeU16(p.ofs().tid, n.tid);\n    p.writeU16(p.ofs().sid, n.sid);\n    p.writeU32(0x10, n.experience);\n\n    std::uint8_t targetLevel = n.level;\n\n    // Network PK7 has no party-format level byte.\n    if (targetLevel < 1 || targetLevel > 100) {\n        if (isLGPE(g)) {\n            reason =\n                "PK7 -> LGPE level calculation is not enabled in this build.";\n            return false;\n        }\n\n        targetLevel = levelFromExperience(\n            n.experience,\n            targetGrowthRate(g, n.species, n.form));\n    }\n\n    if (targetLevel < 1)\n        targetLevel = 1;\n    if (targetLevel > 100)\n        targetLevel = 100;\n\n    if (p.ofs().levelByte >= 0)\n        p.data[p.ofs().levelByte] = targetLevel;\n\n    if (isLGPE(g)) {\n        // PB7 ability is a u8 field. LGPE gameplay has no abilities.\n        p.data[p.ofs().ability] = 0;\n    }\n    else {\n        p.writeU16(p.ofs().ability, ability);\n\n        // Ability number = slot 1.\n        p.data[0x16] =\n            static_cast<std::uint8_t>(\n                (p.data[0x16] & ~0x07) | 1);\n    }\n\n    p.writeU32(p.ofs().pid, n.pid);\n\n    if (p.ofs().nature >= 0)\n        p.data[p.ofs().nature] = n.nature;\n\n    // Stat nature exists in PK8/PB8/PK9 but not PB7.\n    if (!isLGPE(g))\n        p.data[0x21] = n.nature;\n\n    setModernFlags(p, n);\n\n    for (int i = 0; i < 6; ++i) {\n        // Do not reinterpret LGPE AVs as EVs in either direction.\n        p.data[p.ofs().evBase + i] =\n            (n.sourceIsLGPE || isLGPE(g)) ? 0 : n.ev[i];\n    }\n\n    copyUtf16Field(n.nickname, p, p.ofs().nickname);\n    copyUtf16Field(n.htName, p, p.ofs().htName);\n    copyUtf16Field(n.otName, p, p.ofs().otName);\n\n    const int m = p.ofs().moveBase;\n\n    for (int i = 0; i < 4; ++i) {\n        p.writeU16(m + i * 2, n.moves[i]);\n        p.data[m + 8 + i] = n.movePP[i];\n        p.data[m + 12 + i] = n.movePPUps[i];\n        p.writeU16(m + 16 + i * 2, n.relearnMoves[i]);\n    }\n\n    p.writeU32(p.ofs().iv32, n.iv32);\n\n    if (p.ofs().languageByte >= 0)\n        p.data[p.ofs().languageByte] = n.language;\n\n    if (p.ofs().ball >= 0)\n        p.data[p.ofs().ball] = n.ball;\n\n    // Backwards structural clones are not HOME transfers. Use the target\n    // game\'s own version value so an older format does not contain a newer\n    // game-version byte it cannot represent consistently.\n    const std::uint8_t targetVersion =\n        gameVersionValue(g);\n\n    if (isLGPE(g))\n        p.data[0xDF] = targetVersion;\n    else if (isSV(g))\n        p.data[0xCE] = targetVersion;\n    else\n        p.data[0xDE] = targetVersion;\n\n    if (isSV(g)) {\n        // Deterministic Normal Tera policy.\n        p.data[0x94] = 0;\n        p.data[0x95] = 0;\n\n        // Explicit offline-clone policy.\n        std::memset(\n            p.data.data() + 0x127,\n            0,\n            sizeof(std::uint64_t));\n    }\n\n    p.refreshChecksum();\n\n    out = p;\n    reason.clear();\n    return true;\n}\n\nbool recvAll(\n    int fd,\n    void* data,\n    std::size_t size)\n{\n    auto* p = static_cast<std::uint8_t*>(data);\n\n    while (size) {\n        const ssize_t n = ::recv(fd, p, size, 0);\n\n        if (n < 0) {\n            if (errno == EINTR)\n                continue;\n            return false;\n        }\n\n        if (!n)\n            return false;\n\n        p += n;\n        size -= static_cast<std::size_t>(n);\n    }\n\n    return true;\n}\n\nbool receivePk7(\n    const char* ip,\n    std::array<std::uint8_t, cloneproto::PK7_SIZE>& out)\n{\n    const int fd =\n        ::socket(AF_INET, SOCK_STREAM, 0);\n\n    if (fd < 0)\n        return false;\n\n    sockaddr_in s{};\n    s.sin_family = AF_INET;\n    s.sin_port = htons(cloneproto::TCP_PORT);\n\n    if (inet_pton(AF_INET, ip, &s.sin_addr) != 1 ||\n        ::connect(\n            fd,\n            reinterpret_cast<sockaddr*>(&s),\n            sizeof(s)) < 0) {\n\n        ::close(fd);\n        return false;\n    }\n\n    std::uint8_t hdr[cloneproto::HDR_SIZE];\n\n    const bool ok =\n        recvAll(fd, hdr, sizeof(hdr)) &&\n        cloneproto::validateHeader(hdr) &&\n        recvAll(fd, out.data(), out.size());\n\n    ::shutdown(fd, SHUT_RDWR);\n    ::close(fd);\n\n    return ok;\n}\n\nvoid waitForB()\n{\n    std::printf("\\nB = back\\n");\n    consoleUpdate(nullptr);\n\n    while (appletMainLoop()) {\n        padUpdate(&g_pad);\n        consoleUpdate(nullptr);\n\n        if (padGetButtonsDown(&g_pad) & HidNpadButton_B)\n            break;\n    }\n}\n\nvoid showCopyResultAndWait(\n    bool success,\n    GameType sourceGame,\n    const BoxSlot& sourceSlot,\n    GameType targetGame,\n    const BoxSlot& targetSlot)\n{\n    consoleClear();\n\n    if (success) {\n        std::printf(\n            "COPY COMPLETE\\n"\n            "=============\\n\\n"\n            "%s\\n"\n            "Box %02d / Slot %02d\\n"\n            "        ->\\n"\n            "%s\\n"\n            "Box %02d / Slot %02d\\n\\n"\n            "The clone was written successfully.\\n"\n            "The source Pokemon remains unchanged.\\n\\n"\n            "Press A or B to return.\\n",\n            gameInfo(sourceGame).displayName,\n            sourceSlot.box + 1,\n            sourceSlot.slot + 1,\n            gameInfo(targetGame).displayName,\n            targetSlot.box + 1,\n            targetSlot.slot + 1);\n    } else {\n        std::printf(\n            "COPY FAILED\\n"\n            "===========\\n\\n"\n            "The destination could not be written.\\n"\n            "The source Pokemon remains unchanged.\\n\\n"\n            "Press A or B to return.\\n");\n    }\n\n    consoleUpdate(nullptr);\n\n    while (appletMainLoop()) {\n        padUpdate(&g_pad);\n        consoleUpdate(nullptr);\n\n        const u64 d = padGetButtonsDown(&g_pad);\n        if ((d & HidNpadButton_A) || (d & HidNpadButton_B))\n            break;\n    }\n}\n\n\n\nGameType selectGame(\n    const char* title,\n    const GameType* games,\n    std::size_t count)\n{\n    std::size_t selected = 0;\n\n    while (appletMainLoop()) {\n        consoleClear();\n\n        std::printf("%s\\n\\n", title);\n\n        for (std::size_t i = 0; i < count; ++i) {\n            std::printf(\n                "%c %s\\n",\n                i == selected ? \'>\' : \' \',\n                gameInfo(games[i]).displayName);\n        }\n\n        std::printf(\n            "\\nD-Pad = select\\n"\n            "A = confirm\\n");\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_Up)\n            selected =\n                (selected + count - 1) % count;\n\n        if (d & HidNpadButton_Down)\n            selected =\n                (selected + 1) % count;\n\n        if (d & HidNpadButton_A)\n            return games[selected];\n    }\n\n    return games[0];\n}\n\nBoxSlot selectBoxSlot(\n    const char* title,\n    int boxes,\n    int slots)\n{\n    BoxSlot v{};\n\n    while (appletMainLoop()) {\n        consoleClear();\n\n        std::printf(\n            "%s\\n\\n"\n            "Box : %d / %d\\n"\n            "Slot: %d / %d\\n\\n"\n            "Up/Down = box\\n"\n            "Left/Right = slot\\n"\n            "A = confirm\\n",\n            title,\n            v.box + 1,\n            boxes,\n            v.slot + 1,\n            slots);\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_Up)\n            v.box =\n                (v.box + boxes - 1) % boxes;\n\n        if (d & HidNpadButton_Down)\n            v.box =\n                (v.box + 1) % boxes;\n\n        if (d & HidNpadButton_Left)\n            v.slot =\n                (v.slot + slots - 1) % slots;\n\n        if (d & HidNpadButton_Right)\n            v.slot =\n                (v.slot + 1) % slots;\n\n        if (d & HidNpadButton_A)\n            return v;\n    }\n\n    return v;\n}\n\nbool askIp(\n    char* out,\n    std::size_t len)\n{\n    SwkbdConfig k{};\n\n    if (R_FAILED(swkbdCreate(&k, 0)))\n        return false;\n\n    swkbdConfigMakePresetDefault(&k);\n\n    swkbdConfigSetGuideText(\n        &k,\n        "Enter the 3DS IPv4 address");\n\n    swkbdConfigSetInitialText(\n        &k,\n        "192.168.1.");\n\n    const Result rc =\n        swkbdShow(&k, out, len);\n\n    swkbdClose(&k);\n\n    return R_SUCCEEDED(rc);\n}\n\nbool injectDestination(\n    AccountUid uid,\n    GameType g,\n    const BoxSlot& dst,\n    const Pokemon& mon)\n{\n    const auto& info =\n        gameInfo(g);\n\n    Result rc =\n        fsdevMountSaveData(\n            "dst",\n            info.titleId,\n            uid);\n\n    if (R_FAILED(rc)) {\n        std::printf(\n            "Target mount failed: 0x%08X\\n",\n            rc);\n        return false;\n    }\n\n    const std::string path =\n        std::string("dst:/") +\n        info.saveFileName;\n\n    SaveFile save;\n    save.setGameType(g);\n\n    if (!save.load(path) ||\n        dst.box >= save.boxCount() ||\n        dst.slot >= save.slotsPerBox()) {\n\n        fsdevUnmountDevice("dst");\n        return false;\n    }\n\n    save.setBoxSlot(\n        dst.box,\n        dst.slot,\n        mon);\n\n    if (!save.save(path)) {\n        fsdevUnmountDevice("dst");\n        return false;\n    }\n\n    rc = fsdevCommitDevice("dst");\n\n    fsdevUnmountDevice("dst");\n\n    return R_SUCCEEDED(rc);\n}\n\nvoid modeNetwork(AccountUid uid)\n{\n    // Network PK7 -> modern Switch targets.\n    // LGPE is intentionally omitted because network PK7 level conversion\n    // for PB7 is not enabled in this build.\n    constexpr GameType targets[] = {\n        GameType::Sw,\n        GameType::Sh,\n        GameType::BD,\n        GameType::SP,\n        GameType::S,\n        GameType::V\n    };\n\n    const GameType g =\n        selectGame(\n            "MODE A - Target",\n            targets,\n            std::size(targets));\n\n    const auto& info =\n        gameInfo(g);\n\n    const BoxSlot dst =\n        selectBoxSlot(\n            "Destination slot",\n            info.boxCount,\n            info.slotsPerBox);\n\n    char ip[32]{};\n\n    consoleClear();\n    consoleUpdate(nullptr);\n\n    if (!askIp(ip, sizeof(ip))) {\n        waitForB();\n        return;\n    }\n\n    consoleClear();\n\n    std::printf(\n        "Receiving plaintext PK7 from\\n"\n        "%s:%u...\\n",\n        ip,\n        cloneproto::TCP_PORT);\n\n    consoleUpdate(nullptr);\n\n    std::array<\n        std::uint8_t,\n        cloneproto::PK7_SIZE\n    > pk7{};\n\n    if (!receivePk7(ip, pk7)) {\n        std::printf("Receive failed.\\n");\n        waitForB();\n        return;\n    }\n\n    NeutralMon n;\n    Pokemon clone;\n    std::string reason;\n\n    if (!neutralFromPlainPk7(pk7, n) ||\n        !makeTargetPokemon(\n            n,\n            g,\n            clone,\n            reason)) {\n\n        std::printf(\n            "Conversion failed:\\n%s\\n",\n            reason.c_str());\n\n        waitForB();\n        return;\n    }\n\n    std::printf(\n        injectDestination(\n            uid,\n            g,\n            dst,\n            clone)\n            ? "Clone written. 3DS source unchanged.\\n"\n            : "Target write failed.\\n");\n\n    waitForB();\n}\n\n\n\n\n\n\n\nstd::string uiSpeciesName(std::uint16_t id)\n{\n    return std::string(UiNames::species(id));\n}\n\nstd::string uiMoveName(std::uint16_t id)\n{\n    return std::string(UiNames::move(id));\n}\n\nstd::string uiNatureName(std::uint8_t id)\n{\n    return std::string(UiNames::nature(id));\n}\n\nstd::string uiAbilityName(std::uint16_t id)\n{\n    return std::string(UiNames::ability(id));\n}\n\nstd::string uiItemName(std::uint16_t id)\n{\n    return std::string(UiNames::item(id));\n}\n\nconst char* genderText(std::uint8_t gender)\n{\n    switch (gender) {\n        case 0: return "Male";\n        case 1: return "Female";\n        default: return "Genderless";\n    }\n}\n\nvoid printPokemonInfo(\n    const Pokemon& mon,\n    const SaveFile& save,\n    GameType game,\n    int box,\n    int slot)\n{\n    if (mon.isEmpty()) {\n        std::printf(\n            "Selected slot: EMPTY\\n");\n        return;\n    }\n\n    const int partyIndex =\n        isLGPE(game)\n            ? save.lgpePartyIndexOf(box, slot)\n            : -1;\n\n    const std::string& speciesName =\n        uiSpeciesName(mon.species());\n\n    const std::string nickname =\n        mon.isNicknamed()\n            ? mon.nickname()\n            : std::string("---");\n\n    const std::string& natureName =\n        uiNatureName(mon.nature());\n\n    const std::uint16_t abilityId =\n        mon.ability();\n\n    const std::uint16_t itemId =\n        mon.heldItem();\n\n    const bool lgpe =\n        isLGPE(game);\n\n    const std::string abilityName =\n        lgpe\n            ? std::string("N/A (LGPE)")\n            : (abilityId\n                ? uiAbilityName(abilityId)\n                : std::string("---"));\n\n    const std::string itemName =\n        lgpe\n            ? std::string("N/A (LGPE)")\n            : (itemId\n                ? uiItemName(itemId)\n                : std::string("---"));\n\n    const std::uint16_t moves[4] = {\n        mon.move1(),\n        mon.move2(),\n        mon.move3(),\n        mon.move4()\n    };\n\n    std::printf(\n        "Species: %s%s\\n"\n        "Nickname: %s\\n"\n        "Dex: #%03u   Level: %u\\n"\n        "Gender: %s   Shiny: %s\\n"\n        "OT: %s   TID: %06u\\n"\n        "Nature: %s (#%u)\\n"\n        "Ability: %s%s\\n"\n        "Held Item: %s%s\\n"\n        "Moves:\\n",\n        speciesName.c_str(),\n        partyIndex >= 0 ? "  [PARTY]" : "",\n        nickname.c_str(),\n        static_cast<unsigned>(mon.species()),\n        static_cast<unsigned>(mon.level()),\n        genderText(mon.gender()),\n        mon.isShiny() ? "YES" : "NO",\n        mon.otName().c_str(),\n        static_cast<unsigned>(mon.displayTid()),\n        natureName.c_str(),\n        static_cast<unsigned>(mon.nature()),\n        abilityName.c_str(),\n        lgpe\n            ? ""\n            : (" (#" + std::to_string(\n                static_cast<unsigned>(abilityId)) + ")").c_str(),\n        itemName.c_str(),\n        lgpe\n            ? ""\n            : (" (#" + std::to_string(\n                static_cast<unsigned>(itemId)) + ")").c_str());\n\n    for (int i = 0; i < 4; ++i) {\n        if (moves[i] == 0) {\n            std::printf(\n                "  %d. ---\\n",\n                i + 1);\n        }\n        else {\n            std::printf(\n                "  %d. %s (#%u)\\n",\n                i + 1,\n                uiMoveName(moves[i]).c_str(),\n                static_cast<unsigned>(moves[i]));\n        }\n    }\n\n    std::printf(\n        "IVs H/A/D/S/SA/SD:\\n"\n        "%d / %d / %d / %d / %d / %d\\n",\n        mon.ivHp(),\n        mon.ivAtk(),\n        mon.ivDef(),\n        mon.ivSpe(),\n        mon.ivSpA(),\n        mon.ivSpD());\n\n    if (partyIndex >= 0) {\n        std::printf(\n            "LGPE Party Position: %d\\n",\n            partyIndex + 1);\n    }\n}\n\n\nbool selectLoadedSaveSlot(\n    const SaveFile& save,\n    GameType game,\n    const char* title,\n    bool requirePokemon,\n    bool protectLGPEParty,\n    BoxSlot& result)\n{\n    int box = 0;\n    int slot = 0;\n\n    const int boxes = save.boxCount();\n    const int slots = save.slotsPerBox();\n\n    if (boxes <= 0 || slots <= 0)\n        return false;\n\n    while (appletMainLoop()) {\n        const Pokemon mon =\n            save.getBoxSlot(box, slot);\n\n        const int partyIndex =\n            isLGPE(game)\n                ? save.lgpePartyIndexOf(box, slot)\n                : -1;\n\n        consoleClear();\n\n        std::printf(\n            "%s\\n"\n            "===============================\\n\\n"\n            "%s\\n",\n            title,\n            gameInfo(game).displayName);\n\n        if (isLGPE(game)) {\n            std::printf(\n                "LGPE STORAGE MODE\\n"\n                "40 virtual boxes x 25 slots\\n"\n                "Flat storage index: %d / 1000\\n\\n",\n                box * slots + slot + 1);\n        }\n        else {\n            const std::string boxName =\n                save.getBoxName(box);\n\n            std::printf(\n                "Box name: %s\\n\\n",\n                boxName.c_str());\n        }\n\n        std::printf(\n            "Box: %02d / %02d\\n"\n            "Slot: %02d / %02d\\n\\n",\n            box + 1,\n            boxes,\n            slot + 1,\n            slots);\n\n        printPokemonInfo(\n            mon,\n            save,\n            game,\n            box,\n            slot);\n\n        std::printf(\n            "\\n-------------------------------\\n");\n\n        bool canSelect = true;\n\n        if (requirePokemon && mon.isEmpty()) {\n            canSelect = false;\n            std::printf(\n                "Status: EMPTY - choose a Pokemon.\\n");\n        }\n        else if (protectLGPEParty &&\n                 partyIndex >= 0) {\n            canSelect = false;\n            std::printf(\n                "Status: PROTECTED LGPE PARTY SLOT\\n"\n                "Choose another destination slot.\\n");\n        }\n        else if (!requirePokemon &&\n                 !mon.isEmpty()) {\n            std::printf(\n                "Status: OCCUPIED\\n"\n                "A will choose this slot for overwrite.\\n");\n        }\n        else {\n            std::printf(\n                "Status: READY\\n");\n        }\n\n        if (canSelect)\n            std::printf("A = select this slot\\n");\n\n        std::printf(\n            "Up/Down = box\\n"\n            "Left/Right = slot\\n"\n            "L/R = previous/next box\\n"\n            "B = cancel\\n");\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_Up)\n            box = (box + boxes - 1) % boxes;\n\n        if (d & HidNpadButton_Down)\n            box = (box + 1) % boxes;\n\n        if (d & HidNpadButton_Left)\n            slot = (slot + slots - 1) % slots;\n\n        if (d & HidNpadButton_Right)\n            slot = (slot + 1) % slots;\n\n        if (d & HidNpadButton_L)\n            box = (box + boxes - 1) % boxes;\n\n        if (d & HidNpadButton_R)\n            box = (box + 1) % boxes;\n\n        if (d & HidNpadButton_B)\n            return false;\n\n        if ((d & HidNpadButton_A) &&\n            canSelect) {\n\n            result.box = box;\n            result.slot = slot;\n            return true;\n        }\n    }\n\n    return false;\n}\n\nbool selectDestinationSlotPreview(\n    AccountUid uid,\n    GameType targetGame,\n    BoxSlot& result)\n{\n    const auto& info =\n        gameInfo(targetGame);\n\n    Result rc =\n        fsdevMountSaveDataReadOnly(\n            "dstpreview",\n            info.titleId,\n            uid);\n\n    if (R_FAILED(rc)) {\n        consoleClear();\n        std::printf(\n            "Could not open destination save\\n"\n            "for preview.\\n"\n            "Result: 0x%08X\\n",\n            rc);\n        consoleUpdate(nullptr);\n        waitForB();\n        return false;\n    }\n\n    const std::string path =\n        std::string("dstpreview:/") +\n        info.saveFileName;\n\n    SaveFile preview;\n    preview.setGameType(targetGame);\n\n    if (!preview.load(path)) {\n        fsdevUnmountDevice("dstpreview");\n\n        consoleClear();\n        std::printf(\n            "Could not load destination save.\\n");\n        consoleUpdate(nullptr);\n        waitForB();\n        return false;\n    }\n\n    const bool selected =\n        selectLoadedSaveSlot(\n            preview,\n            targetGame,\n            "DESTINATION SLOT",\n            false,\n            true,\n            result);\n\n    fsdevUnmountDevice("dstpreview");\n    return selected;\n}\n\nbool selectCompatibleTarget(\n    const NeutralMon& mon,\n    const GameType* games,\n    std::size_t count,\n    GameType& result)\n{\n    std::size_t selected = 0;\n\n    while (appletMainLoop()) {\n        consoleClear();\n\n        std::printf(\n            "CHOOSE DESTINATION\\n"\n            "==================\\n\\n"\n            "Selected Pokemon:\\n"\n            "Species: %s%s\\n"\n            "Nickname: %s\\n"\n            "Dex #%03u   Lv.%u   %s\\n"\n            "OT: %s\\n"\n            "Ability: %s (#%u)\\n"\n            "Held Item: %s (#%u)\\n\\n",\n            mon.sourceSpeciesName.empty()\n                ? "???"\n                : mon.sourceSpeciesName.c_str(),\n            mon.sourceShiny ? "  [SHINY]" : "",\n            mon.sourceNickname.empty()\n                ? "---"\n                : mon.sourceNickname.c_str(),\n            static_cast<unsigned>(mon.species),\n            static_cast<unsigned>(mon.level),\n            genderText(mon.gender),\n            mon.sourceOT.empty()\n                ? "(unknown)"\n                : mon.sourceOT.c_str(),\n            mon.sourceAbility\n                ? uiAbilityName(\n                    mon.sourceAbility).c_str()\n                : "---",\n            static_cast<unsigned>(mon.sourceAbility),\n            mon.sourceHeldItem\n                ? uiItemName(\n                    mon.sourceHeldItem).c_str()\n                : "---",\n            static_cast<unsigned>(mon.sourceHeldItem));\n\n        for (std::size_t i = 0; i < count; ++i) {\n            std::string reason;\n            const bool compatible =\n                targetSupportsPokemon(\n                    games[i],\n                    mon.species,\n                    mon.form,\n                    reason);\n\n            std::printf(\n                "%s [%s] %s\\n",\n                i == selected ? ">>>" : "   ",\n                compatible ? "YES" : "NO ",\n                gameInfo(games[i]).displayName);\n        }\n\n        std::string selectedReason;\n        const bool selectedCompatible =\n            targetSupportsPokemon(\n                games[selected],\n                mon.species,\n                mon.form,\n                selectedReason);\n\n        std::printf(\n            "\\n--------------------------------\\n"\n            "Highlighted:\\n%s\\n\\n",\n            gameInfo(games[selected]).displayName);\n\n        if (selectedCompatible) {\n            std::printf(\n                "Status: COMPATIBLE\\n"\n                "A = choose this destination\\n");\n        }\n        else {\n            std::printf(\n                "Status: NOT COMPATIBLE\\n"\n                "%s\\n"\n                "Choose another game.\\n",\n                selectedReason.c_str());\n        }\n\n        std::printf(\n            "\\nUp/Down = select\\n"\n            "B = cancel\\n");\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_Up)\n            selected =\n                (selected + count - 1) % count;\n\n        if (d & HidNpadButton_Down)\n            selected =\n                (selected + 1) % count;\n\n        if (d & HidNpadButton_B)\n            return false;\n\n        if ((d & HidNpadButton_A) &&\n            selectedCompatible) {\n\n            result = games[selected];\n            return true;\n        }\n    }\n\n    return false;\n}\n\nbool confirmUniversalCopy(\n    GameType sourceGame,\n    const BoxSlot& sourceSlot,\n    const NeutralMon& mon,\n    GameType targetGame,\n    const BoxSlot& targetSlot)\n{\n    while (appletMainLoop()) {\n        consoleClear();\n\n        std::printf(\n            "CONFIRM COPY\\n"\n            "============\\n\\n"\n            "SOURCE - READ ONLY\\n"\n            "%s\\n"\n            "Species: %s%s\\n"\n            "Nickname: %s\\n"\n            "Box %02d / Slot %02d\\n"\n            "Dex #%03u   Lv.%u\\n\\n"\n            "DESTINATION\\n"\n            "%s\\n"\n            "Box %02d / Slot %02d\\n\\n"\n            "Compatibility: YES\\n"\n            "Source will remain unchanged.\\n\\n",\n            gameInfo(sourceGame).displayName,\n            mon.sourceSpeciesName.empty()\n                ? "???"\n                : mon.sourceSpeciesName.c_str(),\n            mon.sourceShiny ? "  [SHINY]" : "",\n            mon.sourceNickname.empty()\n                ? "---"\n                : mon.sourceNickname.c_str(),\n            sourceSlot.box + 1,\n            sourceSlot.slot + 1,\n            static_cast<unsigned>(mon.species),\n            static_cast<unsigned>(mon.level),\n            gameInfo(targetGame).displayName,\n            targetSlot.box + 1,\n            targetSlot.slot + 1);\n\n        if (sourceGame == targetGame &&\n            sourceSlot.box == targetSlot.box &&\n            sourceSlot.slot == targetSlot.slot) {\n\n            std::printf(\n                "WARNING:\\n"\n                "Source and destination are the same slot.\\n"\n                "Choose B and select another destination.\\n\\n");\n        }\n        else {\n            std::printf("A = COPY NOW\\n");\n        }\n\n        std::printf("B = cancel\\n");\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_B)\n            return false;\n\n        if ((d & HidNpadButton_A) &&\n            !(sourceGame == targetGame &&\n              sourceSlot.box == targetSlot.box &&\n              sourceSlot.slot == targetSlot.slot)) {\n\n            return true;\n        }\n    }\n\n    return false;\n}\n\n\nvoid modeUniversalLocal(AccountUid uid)\n{\n    constexpr GameType games[] = {\n        GameType::GP,\n        GameType::GE,\n        GameType::Sw,\n        GameType::Sh,\n        GameType::BD,\n        GameType::SP,\n        GameType::S,\n        GameType::V\n    };\n\n    // ------------------------------------------------------------\n    // 1. Choose the source game.\n    // ------------------------------------------------------------\n    const GameType sourceGame =\n        selectGame(\n            "LOCAL COPY - SOURCE GAME",\n            games,\n            std::size(games));\n\n    const auto& sourceInfo =\n        gameInfo(sourceGame);\n\n    // ------------------------------------------------------------\n    // 2. Mount and load source READ ONLY before choosing a slot.\n    //    This lets the selector use the real geometry:\n    //      LGPE = 40 x 25\n    //      SwSh = 32 x 30\n    //      BDSP = 40 x 30\n    //      SV   = 32 x 30\n    // ------------------------------------------------------------\n    Result rc =\n        fsdevMountSaveDataReadOnly(\n            "src",\n            sourceInfo.titleId,\n            uid);\n\n    if (R_FAILED(rc)) {\n        consoleClear();\n        std::printf(\n            "Read-only source mount failed:\\\\n"\n            "0x%08X\\\\n",\n            rc);\n        consoleUpdate(nullptr);\n        waitForB();\n        return;\n    }\n\n    const std::string sourcePath =\n        std::string("src:/") +\n        sourceInfo.saveFileName;\n\n    SaveFile sourceSave;\n    sourceSave.setGameType(sourceGame);\n\n    if (!sourceSave.load(sourcePath)) {\n        fsdevUnmountDevice("src");\n\n        consoleClear();\n        std::printf(\n            "Source save load failed.\\\\n");\n        consoleUpdate(nullptr);\n        waitForB();\n        return;\n    }\n\n    BoxSlot sourceSlot{};\n\n    if (!selectLoadedSaveSlot(\n            sourceSave,\n            sourceGame,\n            "SELECT SOURCE POKEMON",\n            true,\n            false,\n            sourceSlot)) {\n\n        fsdevUnmountDevice("src");\n        return;\n    }\n\n    const Pokemon sourcePokemon =\n        sourceSave.getBoxSlot(\n            sourceSlot.box,\n            sourceSlot.slot);\n\n    NeutralMon neutral;\n\n    if (sourcePokemon.isEmpty() ||\n        !neutralFromPokemon(\n            sourcePokemon,\n            neutral)) {\n\n        fsdevUnmountDevice("src");\n\n        consoleClear();\n        std::printf(\n            "Could not decode source Pokemon.\\\\n");\n        consoleUpdate(nullptr);\n        waitForB();\n        return;\n    }\n\n    // Source Pokemon data is now copied into NeutralMon.\n    // Unmount source before any destination save is opened.\n    fsdevUnmountDevice("src");\n\n    // ------------------------------------------------------------\n    // 3. Pick only from compatible target games.\n    // ------------------------------------------------------------\n    GameType targetGame{};\n\n    if (!selectCompatibleTarget(\n            neutral,\n            games,\n            std::size(games),\n            targetGame)) {\n\n        return;\n    }\n\n    // ------------------------------------------------------------\n    // 4. Preview the ACTUAL destination save read-only.\n    //    LGPE uses 25 slots per virtual box and party-linked slots\n    //    are protected from accidental overwrite.\n    // ------------------------------------------------------------\n    BoxSlot targetSlot{};\n\n    if (!selectDestinationSlotPreview(\n            uid,\n            targetGame,\n            targetSlot)) {\n\n        return;\n    }\n\n    std::string reason;\n    Pokemon clone;\n\n    const bool compatible =\n        makeTargetPokemon(\n            neutral,\n            targetGame,\n            clone,\n            reason);\n\n    if (!compatible) {\n        consoleClear();\n\n        std::printf(\n            "COPY CANCELLED\\\\n"\n            "==============\\\\n\\\\n"\n            "%s\\\\n\\\\n"\n            "Source remains unchanged.\\\\n",\n            reason.c_str());\n\n        consoleUpdate(nullptr);\n        waitForB();\n        return;\n    }\n\n    // ------------------------------------------------------------\n    // 5. Final source/destination summary.\n    // ------------------------------------------------------------\n    if (!confirmUniversalCopy(\n            sourceGame,\n            sourceSlot,\n            neutral,\n            targetGame,\n            targetSlot)) {\n\n        return;\n    }\n\n    consoleClear();\n\n    std::printf(\n        "COPYING...\\\\n\\\\n"\n        "%s\\\\n"\n        "%s\\\\n"\n        "Box %02d / Slot %02d\\\\n"\n        "        ->\\\\n"\n        "%s\\\\n"\n        "Box %02d / Slot %02d\\\\n\\\\n",\n        neutral.sourceDisplayName.empty()\n            ? "Selected Pokemon"\n            : neutral.sourceDisplayName.c_str(),\n        sourceInfo.displayName,\n        sourceSlot.box + 1,\n        sourceSlot.slot + 1,\n        gameInfo(targetGame).displayName,\n        targetSlot.box + 1,\n        targetSlot.slot + 1);\n\n    consoleUpdate(nullptr);\n\n    const bool written =\n        injectDestination(\n            uid,\n            targetGame,\n            targetSlot,\n            clone);\n\n    showCopyResultAndWait(\n        written,\n        sourceGame,\n        sourceSlot,\n        targetGame,\n        targetSlot);\n}\n\n\n\n} // namespace\n\nint main(int argc, char** argv)\n{\n    consoleInit(nullptr);\n\n    padConfigureInput(\n        1,\n        HidNpadStyleSet_NpadStandard);\n\n    padInitializeDefault(&g_pad);\n\n    Result rc =\n        socketInitializeDefault();\n\n    if (R_FAILED(rc))\n        return 1;\n\n    rc =\n        accountInitialize(\n            AccountServiceType_Application);\n\n    if (R_FAILED(rc)) {\n        socketExit();\n        return 1;\n    }\n\n    AccountUid uid{};\n\n    rc =\n        accountGetPreselectedUser(&uid);\n\n    if (R_FAILED(rc))\n        rc =\n            accountGetLastOpenedUser(&uid);\n\n    if (R_FAILED(rc)) {\n        std::printf(\n            "Could not determine user account.\\n");\n\n        accountExit();\n        socketExit();\n        return 1;\n    }\n\n    int selection = 0;\n    bool running = true;\n\n    while (running && appletMainLoop()) {\n        consoleClear();\n\n        std::printf(\n            "Pokemon Save Clone Tool\\n"\n            "=======================\\n\\n"\n            "%c MODE A - Network 3DS -> Switch\\n"\n            "%c MODE B - Universal Switch Copy\\n"\n            "%c Exit\\n\\n"\n            "COPY-BASED source access only.\\n\\n"\n            "Mode B supports:\\n"\n            "LGPE / SwSh / BDSP / SV\\n\\n"\n            "Up/Down = select\\n"\n            "A = enter\\n"\n            "+ = exit\\n",\n            selection == 0 ? \'>\' : \' \',\n            selection == 1 ? \'>\' : \' \',\n            selection == 2 ? \'>\' : \' \');\n\n        consoleUpdate(nullptr);\n        padUpdate(&g_pad);\n\n        const u64 d =\n            padGetButtonsDown(&g_pad);\n\n        if (d & HidNpadButton_Up)\n            selection =\n                (selection + 2) % 3;\n\n        if (d & HidNpadButton_Down)\n            selection =\n                (selection + 1) % 3;\n\n        if (d & HidNpadButton_Plus)\n            running = false;\n\n        if (d & HidNpadButton_A) {\n            if (selection == 0)\n                modeNetwork(uid);\n            else if (selection == 1)\n                modeUniversalLocal(uid);\n            else\n                running = false;\n        }\n    }\n\n    accountExit();\n    socketExit();\n    consoleExit(nullptr);\n\n    return 0;\n}\n', '3ds/include/PKSMCORE_CONFIG.h': '#pragma once\n#define _PKSMCORE_CONFIGURED\n#define _PKSMCORE_LANG_FOLDER "romfs:/strings/"\n#define _PKSMCORE_DISABLE_THREAD_SAFETY\n', '3ds/Makefile': '.SUFFIXES:\n\nifeq ($(strip $(DEVKITARM)),)\n$(error "DEVKITARM is not set")\nendif\n\nTOPDIR ?= $(CURDIR)\n\ninclude $(DEVKITARM)/3ds_rules\n\nTARGET := pokemon_clone_3ds\nBUILD  := build\n\nPKSM   := $(TOPDIR)/../third_party/PKSM-Core\nCOMMON := $(TOPDIR)/../common/include\n\nPKSM_SOURCE_DIRS := $(shell find $(PKSM)/source $(PKSM)/memecrypto -type d)\n\nSOURCES := source $(PKSM_SOURCE_DIRS)\n\nINCLUDES := \\\n\t$(COMMON) \\\n\t$(PKSM)/include \\\n\t$(PKSM)/memecrypto \\\n\t$(PKSM)/pcg-cpp/include\n\nARCH := -march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft\n\nCFLAGS := \\\n\t-g \\\n\t-Wall \\\n\t-O2 \\\n\t-mword-relocations \\\n\t-ffunction-sections \\\n\t-fdata-sections \\\n\t$(ARCH)\n\nCFLAGS += $(INCLUDE) -D__3DS__\n\nCXXFLAGS := \\\n\t$(CFLAGS) \\\n\t-fno-rtti \\\n\t-std=gnu++20\n\nCXXFLAGS += -include $(TOPDIR)/../common/include/PKSMCORE_CONFIG.h\n\nASFLAGS := -g $(ARCH)\n\nLDFLAGS := \\\n\t-specs=3dsx.specs \\\n\t-g \\\n\t$(ARCH) \\\n\t-Wl,-Map,$(notdir $*.map)\n\nLIBS := -lctru -lm\nLIBDIRS := $(CTRULIB)\n\nifneq ($(BUILD),$(notdir $(CURDIR)))\n\nexport OUTPUT := $(CURDIR)/$(TARGET)\nexport TOPDIR := $(CURDIR)\n\nexport VPATH := $(foreach dir,$(SOURCES),$(abspath $(dir)))\nexport DEPSDIR := $(CURDIR)/$(BUILD)\n\nCFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))\nCPPFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))\n\n# Sav2.cpp pulls unrelated Gen-2-only dependencies and is not needed\n# by the Gen-7 sender.\nCPPFILES := $(filter-out Sav2.cpp,$(CPPFILES))\n\nSFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.s)))\n\nexport LD := $(CXX)\n\nexport OFILES := \\\n\t$(CPPFILES:.cpp=.o) \\\n\t$(CFILES:.c=.o) \\\n\t$(SFILES:.s=.o)\n\nexport INCLUDE := \\\n\t$(foreach dir,$(INCLUDES),-I$(abspath $(dir))) \\\n\t$(foreach dir,$(LIBDIRS),-I$(dir)/include) \\\n\t-I$(CURDIR)/$(BUILD)\n\nexport LIBPATHS := $(foreach dir,$(LIBDIRS),-L$(dir)/lib)\n\n.PHONY: all clean $(BUILD)\n\nall: $(BUILD)\n\n$(BUILD):\n\t@[ -d $@ ] || mkdir -p $@\n\t@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile\n\nclean:\n\t@rm -rf $(BUILD) $(TARGET).3dsx $(TARGET).elf $(TARGET).map\n\nelse\n\nDEPENDS := $(OFILES:.o=.d)\n\n.PHONY: all\nall: $(OUTPUT).3dsx\n\n$(OUTPUT).3dsx: $(OUTPUT).elf\n$(OUTPUT).elf: $(OFILES)\n\n-include $(DEPENDS)\n\nendif\n', 'switch/Makefile': '.SUFFIXES:\nifeq ($(strip $(DEVKITPRO)),)\n$(error "DEVKITPRO is not set")\nendif\nTOPDIR ?= $(CURDIR)\ninclude $(DEVKITPRO)/libnx/switch_rules\n\nTARGET := pokemon_clone_switch\nBUILD := build\nPKHOUSE := ../third_party/pkHouse\nCOMMON := ../common/include\nSOURCES := source vendor/pkhouse_core\nINCLUDES := source $(COMMON) $(PKHOUSE)/include\nARCH := -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE\nCFLAGS := -g -Wall -O2 -ffunction-sections -fdata-sections $(ARCH)\nCFLAGS += $(INCLUDE) -D__SWITCH__\nCXXFLAGS := $(CFLAGS) -std=gnu++20\nASFLAGS := -g $(ARCH)\nLDFLAGS := -specs=$(DEVKITPRO)/libnx/switch.specs -g $(ARCH) -Wl,--gc-sections -Wl,-Map,$(notdir $*.map)\nLIBS := -lnx\nLIBDIRS := $(LIBNX)\n\nifneq ($(BUILD),$(notdir $(CURDIR)))\nexport OUTPUT := $(CURDIR)/$(TARGET)\nexport TOPDIR := $(CURDIR)\nexport VPATH := $(foreach dir,$(SOURCES),$(CURDIR)/$(dir))\nexport DEPSDIR := $(CURDIR)/$(BUILD)\nCFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))\nCPPFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))\nSFILES := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.s)))\nexport LD := $(CXX)\nexport OFILES := $(CPPFILES:.cpp=.o) $(CFILES:.c=.o) $(SFILES:.s=.o)\nexport INCLUDE := $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) $(foreach dir,$(LIBDIRS),-I$(dir)/include) -I$(CURDIR)/$(BUILD)\nexport LIBPATHS := $(foreach dir,$(LIBDIRS),-L$(dir)/lib)\n.PHONY: all clean $(BUILD)\nall: $(BUILD)\n$(BUILD):\n\t@[ -d $@ ] || mkdir -p $@\n\t@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile\nclean:\n\t@rm -rf $(BUILD) $(TARGET).nro $(TARGET).nacp $(TARGET).elf $(TARGET).map\nelse\nDEPENDS := $(OFILES:.o=.d)\n.PHONY: all\nall: $(OUTPUT).nro\n$(OUTPUT).nro: $(OUTPUT).elf $(OUTPUT).nacp\n\t@elf2nro $< $@\n\t@echo "built ... $(notdir $@)"\n$(OUTPUT).elf: $(OFILES)\n-include $(DEPENDS)\nendif\n', 'README.generated.txt': 'Pokemon Save Clone Tool - v7\n\nV7 fixes the persistent ??? text problem seen on real Switch hardware.\n\nThe save data itself was already being read correctly. The problem was that\nruntime ROMFS text lookup was failing in the stripped-down application.\n\nV7 converts pkHouse text files into a C++ header during installation and\ncompiles those names directly into the NRO.\n\nFixes:\n- no runtime ROMFS dependency for displayed names\n- German species names on German Switch system language\n- English fallback\n- compiled move/nature/ability/item names\n- LGPE ability and held item shown as N/A (LGPE)\n\nRetained:\n- LGPE 40 x 25 virtual storage selector\n- LGPE party-slot protection\n- Pokemon details preview\n- universal Switch copy\n- compatibility preview\n- Level 0 fix\n- persistent COPY COMPLETE/COPY FAILED screen\n- read-only source handling\n\nBuild:\n  python setup_pokemon_save_cloner_v7.py --build\n', 'common/include/PKSMCORE_CONFIG.h': '#pragma once\n\n#define _PKSMCORE_GETLINE_FUNC __getline\n#define _PKSMCORE_LANG_FOLDER "romfs:/i18n/"\n#define _PKSMCORE_PERSONAL_FOLDER "romfs:/personal/"\n#define _PKSMCORE_DISABLE_THREAD_SAFETY\n'}
PKHOUSE_CORE_FILES = ['form_names.cpp', 'handler_update.cpp', 'i18n.cpp', 'led.cpp', 'md5.cpp', 'move_types.cpp', 'poke_crypto.cpp', 'pokedex.cpp', 'pokemon.cpp', 'save_file.cpp', 'sc_block.cpp', 'species_converter.cpp', 'swish_crypto.cpp', 'wondercard.cpp']

def log(text):
    print(text, flush=True)

def extract_archive(archive, destination):
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            target = (destination / member.name).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError("Unsafe archive path")
        tf.extractall(destination)
    dirs = [p for p in destination.iterdir() if p.is_dir()]
    if not dirs:
        raise RuntimeError("Archive contained no directory")
    return sorted(dirs)[0]

def fetch(owner, repo, ref, destination):
    log(f"[fetch] {owner}/{repo}@{ref}")
    url = f"https://github.com/{owner}/{repo}/archive/{ref}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="psc-v7_1-") as td:
        td = Path(td)
        archive = td / "archive.tar.gz"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "pokemon-save-cloner-v7.1"},
        )

        with urllib.request.urlopen(req, timeout=90) as response:
            with archive.open("wb") as out:
                shutil.copyfileobj(response, out)

        src = extract_archive(archive, td)

        if destination.exists():
            shutil.rmtree(destination)

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(destination))

def write_project():
    for rel, content in GENERATED_FILES.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

def prepare_pksm():
    pksm = ROOT / "third_party" / "PKSM-Core"

    fetch(
        "FlagBrew",
        "memecrypto",
        MEMECRYPTO_REF,
        pksm / "memecrypto",
    )

    fetch(
        "imneme",
        "pcg-cpp",
        PCG_CPP_REF,
        pksm / "pcg-cpp",
    )

    hdr = pksm / "source" / "i18n" / "i18n_internal.hpp"

    if hdr.is_file():
        text = hdr.read_text(encoding="utf-8")
        text = text.replace(
            "#define _PKSMCORE_GETLINE_FUNC getline",
            "#define _PKSMCORE_GETLINE_FUNC __getline",
        )
        hdr.write_text(text, encoding="utf-8", newline="\n")

def cpp_string(value):
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\t", "\\t")
    return '"' + value + '"'

def read_table(path):
    if not path.is_file():
        raise RuntimeError(f"Name-table file missing: {path}")
    return path.read_text(encoding="utf-8-sig").splitlines()

def array_cpp(name, values):
    rows = ["static constexpr const char* " + name + "[] = {"]
    for value in values:
        rows.append("    " + cpp_string(value) + ",")
    rows.append("};")
    return "\n".join(rows)

def generate_name_header(pkhouse_root):
    data = pkhouse_root / "romfs" / "data"

    species_files = {
        "EN": "species_en.txt",
        "JA": "species_1.txt",
        "FR": "species_3.txt",
        "IT": "species_4.txt",
        "DE": "species_5.txt",
        "ES": "species_7.txt",
        "KO": "species_8.txt",
        "ZHT": "species_9.txt",
        "ZHS": "species_10.txt",
    }

    species_tables = {}

    for code, filename in species_files.items():
        path = data / filename
        if path.is_file():
            species_tables[code] = read_table(path)

    if "EN" not in species_tables:
        raise RuntimeError("English species table missing")

    moves = read_table(data / "moves_en.txt")
    natures = read_table(data / "natures_en.txt")
    abilities = read_table(data / "abilities_en.txt")
    items = read_table(data / "items_en.txt")

    parts = [
        "#pragma once",
        "#include <switch.h>",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        "namespace UiNames {",
        "",
    ]

    for code, values in species_tables.items():
        parts.append(array_cpp("SPECIES_" + code, values))
        parts.append("")

    parts.append(array_cpp("MOVES_EN", moves))
    parts.append("")
    parts.append(array_cpp("NATURES_EN", natures))
    parts.append("")
    parts.append(array_cpp("ABILITIES_EN", abilities))
    parts.append("")
    parts.append(array_cpp("ITEMS_EN", items))
    parts.append("")

    parts.append(r'''
inline int speciesLanguage()
{
    static int cached = -1;

    if (cached >= 0)
        return cached;

    cached = 0;

    if (R_SUCCEEDED(setInitialize())) {
        u64 languageCode{};
        SetLanguage language{};

        if (R_SUCCEEDED(setGetSystemLanguage(&languageCode)) &&
            R_SUCCEEDED(setMakeLanguage(languageCode, &language))) {

            switch (language) {
                case SetLanguage_JA: cached = 1; break;
                case SetLanguage_FR:
                case SetLanguage_FRCA: cached = 2; break;
                case SetLanguage_IT: cached = 3; break;
                case SetLanguage_DE: cached = 4; break;
                case SetLanguage_ES:
                case SetLanguage_ES419: cached = 5; break;
                case SetLanguage_KO: cached = 6; break;
                case SetLanguage_ZHTW:
                case SetLanguage_ZHHANT: cached = 7; break;
                case SetLanguage_ZHCN:
                case SetLanguage_ZHHANS: cached = 8; break;
                default: cached = 0; break;
            }
        }

        setExit();
    }

    return cached;
}

template <std::size_t N>
inline const char* get(
    const char* const (&table)[N],
    std::size_t id)
{
    if (id < N && table[id] && table[id][0])
        return table[id];

    return "Unknown";
}

inline const char* species(std::uint16_t id)
{
    switch (speciesLanguage()) {
''')

    for number, code in [
        (1, "JA"),
        (2, "FR"),
        (3, "IT"),
        (4, "DE"),
        (5, "ES"),
        (6, "KO"),
        (7, "ZHT"),
        (8, "ZHS"),
    ]:
        if code in species_tables:
            parts.append(
                "        case " + str(number) +
                ": return get(SPECIES_" + code + ", id);"
            )

    parts.append("        default: return get(SPECIES_EN, id);")

    parts.append(r'''    }
}

inline const char* move(std::uint16_t id)
{
    return get(MOVES_EN, id);
}

inline const char* nature(std::uint8_t id)
{
    return get(NATURES_EN, id);
}

inline const char* ability(std::uint16_t id)
{
    return get(ABILITIES_EN, id);
}

inline const char* item(std::uint16_t id)
{
    return get(ITEMS_EN, id);
}

} // namespace UiNames
''')

    out = ROOT / "switch" / "source" / "generated_name_tables.hpp"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8", newline="\n")

    log("[names] Compiled Pokemon text tables generated")

def prepare_pkhouse():
    root = ROOT / "third_party" / "pkHouse"
    src = root / "source"
    dst = ROOT / "switch" / "vendor" / "pkhouse_core"

    if dst.exists():
        shutil.rmtree(dst)

    dst.mkdir(parents=True, exist_ok=True)

    for name in PKHOUSE_CORE_FILES:
        source = src / name

        if not source.is_file():
            raise RuntimeError(
                f"Expected pkHouse source missing: {source}"
            )

        shutil.copy2(source, dst / name)

    account = dst / "account.cpp"

    if account.exists():
        account.unlink()

    generate_name_header(root)

def validate():
    sw = (
        ROOT / "switch" / "source" / "main.cpp"
    ).read_text(encoding="utf-8")

    hdr = ROOT / "switch" / "source" / "generated_name_tables.hpp"

    if not hdr.is_file():
        raise RuntimeError(
            "generated_name_tables.hpp was not generated"
        )

    h = hdr.read_text(encoding="utf-8")

    for item in [
        "generated_name_tables.hpp",
        "uiSpeciesName",
        "uiMoveName",
        "uiNatureName",
        "N/A (LGPE)",
        "40 virtual boxes x 25 slots",
        "showCopyResultAndWait",
        "COPY COMPLETE",
        "fsdevMountSaveDataReadOnly",
    ]:
        if item not in sw:
            raise RuntimeError(f"Missing v7.1 feature: {item}")

    for item in [
        "SPECIES_EN",
        "SPECIES_DE",
        "MOVES_EN",
        "NATURES_EN",
        "ABILITIES_EN",
        "ITEMS_EN",
        "SetLanguage_DE",
    ]:
        if item not in h:
            raise RuntimeError(f"Missing compiled name data: {item}")

def run_make(directory):
    subprocess.run(["make", "clean"], cwd=directory, check=False)
    subprocess.run(["make"], cwd=directory, check=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()

    log("[1/9] Writing v7.1 project...")
    write_project()

    log("[2/9] Fetching PKSM-Core...")
    fetch(
        "FlagBrew",
        "PKSM-Core",
        PKSM_REF,
        ROOT / "third_party" / "PKSM-Core",
    )

    log("[3/9] Fetching PKSM dependencies...")
    prepare_pksm()

    log("[4/9] Fetching pkHouse...")
    fetch(
        "Insektaure",
        "pkHouse",
        PKHOUSE_REF,
        ROOT / "third_party" / "pkHouse",
    )

    log("[5/9] Preparing pkHouse core...")
    prepare_pkhouse()

    log("[6/9] Compiled text tables ready.")
    log("[7/9] Validating v7.1...")
    validate()
    log("[8/9] Setup complete.")

    if args.build:
        log("[9/9] Building 3DS + Switch...")

        if not os.environ.get("DEVKITA64"):
            raise RuntimeError(
                "DEVKITA64 is not set. Run:\n"
                "export DEVKITA64=/opt/devkitpro/devkitA64"
            )

        run_make(ROOT / "3ds")
        run_make(ROOT / "switch")
    else:
        log("[9/9] Build skipped.")

    print()
    print("V7.1 COMPLETE")
    print("  - names compiled directly into the NRO")
    print("  - no runtime ROMFS name lookup")
    print("  - German species names on German Switch")
    print("  - LGPE ability/item shown as N/A")
    print()
    print("Expected:")
    print("  3ds/pokemon_clone_3ds.3dsx")
    print("  switch/pokemon_clone_switch.nro")

    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"\nBUILD ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
