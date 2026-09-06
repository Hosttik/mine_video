"""Compile bounded templates to vanilla 1.21.4 functions, running on server ticks."""

import json
import math
import os
import random
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import JobSpec

PACK_FORMAT = 61
MC_VERSION = "1.21.4"
DIMENSION = "minevideo:studio"


@dataclass
class Caption:
    tick: int
    end_tick: int
    text: str


@dataclass
class Plan:
    template: str
    nonce: int
    duration_ticks: int
    title: str
    prepare: list[str] = field(default_factory=list)
    events: dict[int, list[str]] = field(default_factory=dict)
    captions: list[Caption] = field(default_factory=list)
    camera: str = "24 101 30 facing 0 84 0"

    def json(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def arena():
    commands = [
        "kill @e[tag=mv_actor]",
        "forceload add -48 -48 48 48",
        "time set noon", "weather clear", "difficulty normal",
        "gamerule doMobSpawning false", "gamerule doDaylightCycle false",
        "gamerule doWeatherCycle false", "gamerule mobGriefing false",
        "gamerule doFireTick false", "gamerule doEntityDrops false",
        "gamerule doTileDrops false", "gamerule sendCommandFeedback false",
        "gamerule commandBlockOutput false", "gamerule logAdminCommands false",
    ]
    # Every fill stays below vanilla's 32,768 block limit.
    for y in range(80, 112, 8):
        commands.append(f"fill -20 {y} -20 20 {y + 7} 20 minecraft:air")
    commands.extend([
        "fill -20 78 -20 20 78 20 minecraft:bedrock",
        "fill -20 79 -20 20 79 20 minecraft:polished_deepslate",
        "fill -12 79 -12 12 79 12 minecraft:smooth_quartz",
        "fill -13 80 -13 13 82 -13 minecraft:deepslate_bricks",
        "fill -13 80 13 13 82 13 minecraft:deepslate_bricks",
        "fill -13 80 -12 -13 82 12 minecraft:deepslate_bricks",
        "fill 13 80 -12 13 82 12 minecraft:deepslate_bricks",
    ])
    for x in (-12, -6, 0, 6, 12):
        for z in (-12, -6, 0, 6, 12):
            commands.append(f"setblock {x} 79 {z} minecraft:sea_lantern")
    for x in (-16, 16):
        for z in (-16, 16):
            commands.extend([
                f"fill {x} 80 {z} {x} 89 {z} minecraft:quartz_pillar",
                f"setblock {x} 90 {z} minecraft:sea_lantern",
            ])
    return commands


def compile_plan(spec: JobSpec, nonce: int) -> Plan:
    rng = random.Random(spec.seed)
    ru, ticks = spec.language == "ru", spec.duration_seconds * 20
    plan = Plan(spec.template, nonce, ticks, "", prepare=arena())
    if spec.template == "mob_arena":
        plan.title = (f"Голем против {spec.mob_count} кадавров" if ru
                      else f"Can one golem beat {spec.mob_count} husks?")
        plan.prepare.append('summon minecraft:iron_golem 0 80 0 '
                            '{Tags:["mv_actor","mv_left"],NoAI:1b,PersistenceRequired:1b,PlayerCreated:0b}')
        for i in range(spec.mob_count):
            angle = i * math.tau / spec.mob_count + rng.uniform(-0.06, 0.06)
            radius = rng.uniform(6, 9)
            x, z = math.cos(angle) * radius, math.sin(angle) * radius
            plan.prepare.append(f'summon minecraft:husk {x:.3f} 80 {z:.3f} '
                                '{Tags:["mv_actor","mv_right"],NoAI:1b,PersistenceRequired:1b,'
                                'IsBaby:0b,CanPickUpLoot:0b}')
        plan.events[40] = ['execute as @e[tag=mv_actor] run data merge entity @s {NoAI:0b}']
        plan.captions = [Caption(0, 40, plan.title), Caption(40, 90, "БОЙ!" if ru else "FIGHT!")]
    elif spec.template == "tnt_chain":
        plan.title = "Цепная реакция TNT" if ru else "One spark. A chain reaction."
        blocks = rng.choice(["minecraft:oak_planks", "minecraft:cherry_planks", "minecraft:bamboo_planks"])
        for x in range(-9, 10, 3):
            plan.prepare.extend([
                f"fill {x} 80 -3 {x + 1} 86 3 {blocks}",
                f"setblock {x} 80 0 minecraft:tnt",
            ])
        # Later charges have vanilla fuses; explosions can prime nearby TNT sooner.
        plan.events[40] = [
            'setblock -9 80 0 minecraft:air',
            'summon minecraft:tnt -8.5 80 0.5 {Tags:["mv_actor"],fuse:40s}',
        ]
        # A visible second salvo avoids a long empty tail for longer requested clips.
        for i, x in enumerate(range(-9, 10, 3)):
            tick = 100 + i * 12
            plan.events.setdefault(tick, []).append(
                f'summon minecraft:tnt {x + .5} 89 0.5 {{Tags:["mv_actor"],fuse:40s}}')
        # This scene ends after the aftermath, rather than padding to maximum duration.
        plan.duration_ticks = min(ticks, 320)
        plan.captions = [Caption(0, 50, plan.title), Caption(100, 180, "Ещё залп!" if ru else "Second salvo!")]
    else:
        plan.title = "Башня из пустоты" if ru else "Watch a tower build itself"
        palette = rng.choice([
            ("minecraft:deepslate_tiles", "minecraft:cyan_stained_glass"),
            ("minecraft:quartz_bricks", "minecraft:purple_stained_glass"),
            ("minecraft:stone_bricks", "minecraft:orange_stained_glass"),
        ])
        plan.camera = "25 104 32 facing 0 88 0"
        for layer in range(17):
            tick = 40 + round(layer * (ticks - 120) / 16)
            y = 80 + layer
            material = palette[0] if layer % 4 == 0 else palette[1]
            commands = [f"fill -5 {y} -5 5 {y} 5 {material} hollow"]
            # A one-block-thick fill with hollow is a full floor, so carve the interior explicitly.
            if layer % 4:
                commands.append(f"fill -4 {y} -4 4 {y} 4 minecraft:air")
            for x in (-5, 5):
                for z in (-5, 5):
                    commands.append(f"setblock {x} {y} {z} {palette[0]}")
            commands.append(f"particle minecraft:end_rod 0 {y + 1} 0 5 0.3 5 0.02 35 force")
            plan.events[tick] = commands
        plan.captions = [Caption(0, 50, plan.title), Caption(ticks - 75, ticks, "Готово" if ru else "The reveal")]
    if spec.title:
        plan.title = spec.title
        plan.captions[0].text = spec.title
    return plan


def pack_files(plan: Plan, player: str) -> dict[str, str]:
    if not 0 <= plan.nonce <= 2_147_483_647:
        raise ValueError("Invalid nonce")
    # Validate again because this compiler is also a public Python entry point.
    import re
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
        raise ValueError("Invalid Minecraft player name")
    files = {
        "pack.mcmeta": json.dumps({"pack": {"pack_format": PACK_FORMAT, "description": "MineVideo studio 0.1"}}),
        "data/minevideo/dimension/studio.json": json.dumps({
            "type": "minecraft:overworld", "generator": {"type": "minecraft:flat", "settings": {
                "biome": "minecraft:the_void", "lakes": False, "features": False,
                "layers": [{"block": "minecraft:air", "height": 1}], "structure_overrides": [],
            }},
        }),
        "data/minecraft/tags/function/load.json": json.dumps({"values": ["minevideo:load"]}),
        "data/minecraft/tags/function/tick.json": json.dumps({"values": ["minevideo:tick"]}),
    }

    def fn(name, lines):
        files[f"data/minevideo/function/{name}.mcfunction"] = "\n".join(lines) + "\n"

    fn("load", ["scoreboard objectives add mv dummy", f"scoreboard players set #nonce mv {plan.nonce}",
                "scoreboard players set #run mv 0", "scoreboard players set #ready mv 0",
                "scoreboard players set #tick mv 0", "scoreboard players set #result mv 0"])
    camera = f"execute in {DIMENSION} run tp @a[name={player},limit=1] {plan.camera}"
    fn("prepare", [f"execute in {DIMENSION} run function minevideo:prepare_scene",
                   f"gamemode spectator {player}", camera, "scoreboard players set #ready mv 1"])
    fn("prepare_scene", plan.prepare)
    fn("start", ["scoreboard players set #tick mv 0", "scoreboard players set #finish mv -1",
                 "scoreboard players set #result mv 0", "scoreboard players set #run mv 1"])
    fn("tick", [f"execute if score #ready mv matches 1 run {camera}",
                f"execute if score #run mv matches 1 in {DIMENSION} run function minevideo:advance"])
    advance = ["scoreboard players add #tick mv 1"]
    for tick, commands in sorted(plan.events.items()):
        fn(f"event_{tick}", commands)
        advance.append(f"execute if score #tick mv matches {tick} run function minevideo:event_{tick}")
    if plan.template == "mob_arena":
        advance.append("execute if score #tick mv matches 41.. if score #result mv matches 0 run function minevideo:judge")
        fn("judge", [
            'execute unless entity @e[tag=mv_left,nbt={DeathTime:0s}] run scoreboard players set #result mv 2',
            'execute unless entity @e[tag=mv_right,nbt={DeathTime:0s}] run scoreboard players set #result mv 1',
            'execute unless entity @e[tag=mv_left,nbt={DeathTime:0s}] unless entity '
            '@e[tag=mv_right,nbt={DeathTime:0s}] run scoreboard players set #result mv 3',
            "execute if score #result mv matches 1.. run scoreboard players operation #finish mv = #tick mv",
            "execute if score #result mv matches 1.. run scoreboard players add #finish mv 60",
        ])
        advance.append("execute if score #finish mv matches 0.. if score #tick mv >= #finish mv run function minevideo:finish")
    advance.append(f"execute if score #tick mv matches {plan.duration_ticks}.. run function minevideo:finish")
    fn("advance", advance)
    fn("finish", ["scoreboard players set #run mv 2"])
    fn("cleanup", ["scoreboard players set #run mv 0", "scoreboard players set #ready mv 0",
                   f"execute in {DIMENSION} run kill @e[tag=mv_actor]",
                   f"execute in {DIMENSION} run forceload remove -48 -48 48 48"])
    return files


def write_pack(path: Path, plan: Plan, player: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(pack_files(plan, player).items()):
            info = zipfile.ZipInfo(name, date_time=(2024, 12, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    os.replace(tmp, path)


def outcome(spec: JobSpec, result: int):
    if spec.template != "mob_arena":
        return "Сцена завершена" if spec.language == "ru" else "Scene complete"
    labels = ({0: "Время вышло — победитель не определён", 1: "Голем победил", 2: "Кадавры победили", 3: "Ничья"}
              if spec.language == "ru" else
              {0: "Time limit — no winner", 1: "The golem wins", 2: "The husks win", 3: "Draw"})
    return labels[result]
