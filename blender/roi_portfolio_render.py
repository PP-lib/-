# -*- coding: utf-8 -*-
"""
roi_portfolio_render.py
=======================
新工場投資ポートフォリオ（YKK AP Indonesia）の「トータルROI台帳」を
Blender 上で 3D 棒グラフとして可視化・描画するスクリプト。

各テーマ = 1 本の柱:
    - 高さ       : CD 効果 (USD/年)   … 効果の大きさ
    - 底面の一辺 : sqrt(投資額 JPY)     … 底面積 ∝ 投資額
    - 色         : 区分 (基盤/設備/改善) … カテゴリ識別
高い柱ほど効果大・太い柱ほど投資大。Logikal(T1) が突出＝単一障害点が一目で分かる。

--------------------------------------------------------------------------
実行方法（お手元の Blender で）:

  1) ヘッドレスでレンダリング（PNG を出力）:
       blender -b -P roi_portfolio_render.py
     出力先: このスクリプトと同じ場所の ./out/roi_portfolio.png

  2) GUI で組み立てだけ見る（レンダリングしない）:
       blender --python roi_portfolio_render.py -- --no-render

  3) Blender の Scripting タブに貼り付けて実行してもOK。

対応: Blender 3.x / 4.x（EEVEE のエンジン名差異を自動吸収）。
--------------------------------------------------------------------------
"""

import bpy
import bmesh
import math
import os
import sys

# =========================================================================
# 0. 設定（ここを触れば見た目を調整できます）
# =========================================================================
CONFIG = {
    "bar_gap":        1.6,     # 柱の中心間隔 (m)
    "height_scale":   1.0 / 12000.0,  # CD(USD/年) → 高さ(m) の係数
    "width_scale":    1.0 / 6000.0,   # sqrt(投資JPY) → 底面一辺(m) の係数
    "min_width":      0.25,    # 底面一辺の下限 (m)
    "ground_margin":  2.5,     # 地面の余白 (m)
    "render_engine":  "EEVEE", # "EEVEE"（速い）/ "CYCLES"（綺麗・重い）
    "resolution":     (1920, 1080),
    "samples":        64,
    "out_name":       "roi_portfolio.png",
    "show_labels":    True,     # テーマ名・数値ラベルを出すか
}

# =========================================================================
# 1. 台帳データ（トータルROI台帳.md §2 テーマ台帳より）
#    T5 建屋計画は「独立軸・別評価」で数値がないため棒グラフからは除外。
# =========================================================================
# (id, テーマ名, 区分, 投資JPY, 継続費JPY/年, CD_USD/年, 依存)
THEMES = [
    ("T1", "Logikal導入",           "基盤", 10_000_000, 3_000_000, 171_000, []),
    ("T2", "自動切断機・自動加工機", "設備", 90_000_000,         0,  70_000, ["T1", "T3"]),
    ("T3", "UniLink",               "基盤",  8_000_000,         0,  14_000, ["T1"]),
    ("T4", "MES",                   "基盤", 15_000_000, 1_000_000,  20_000, ["T1", "T3"]),
    ("T6", "TBラインのライン化",     "改善",  7_000_000,         0,  35_000, ["T2"]),
    ("T7", "小改善群",               "改善",  3_000_000,         0,  20_000, []),
]

# 区分ごとの色（RGBA, 0..1）
CATEGORY_COLORS = {
    "基盤": (0.12, 0.45, 0.90, 1.0),  # 青
    "設備": (0.95, 0.55, 0.10, 1.0),  # オレンジ
    "改善": (0.20, 0.75, 0.35, 1.0),  # 緑
    "独立軸": (0.55, 0.55, 0.60, 1.0),  # グレー
}


# =========================================================================
# 2. ユーティリティ
# =========================================================================
def clear_scene():
    """既存オブジェクト・不要データを一掃してまっさらにする。"""
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.materials,
                 bpy.data.curves, bpy.data.lights, bpy.data.cameras):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def make_material(name, rgba, roughness=0.4, emission=0.0):
    """Principled BSDF のマテリアルを作る。emission>0 でわずかに自発光。"""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    # Blender 4.x では "Emission" が "Emission Color" にリネームされている
    emit_col = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
    emit_str = bsdf.inputs.get("Emission Strength")
    if emit_col is not None:
        emit_col.default_value = rgba
    if emit_str is not None:
        emit_str.default_value = emission
    return mat


def add_box(name, size_xy, height, location, material):
    """底面 size_xy × size_xy・高さ height の直方体を location(底面中心)に置く。"""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()

    obj.scale = (size_xy, size_xy, height)
    # create_cube は原点中心なので、底面を z=location.z に合わせる
    obj.location = (location[0], location[1], location[2] + height * 0.5)
    obj.data.materials.append(material)

    # スムーズすぎない自然な陰影のため面はフラットのまま
    return obj


def add_text(body, location, size=0.28, align="CENTER",
             rotation=(math.radians(90), 0, 0), material=None):
    """3D テキストを配置。日本語はフォント未設定だと豆腐になるので下記 NOTE 参照。"""
    curve = bpy.data.curves.new(type="FONT", name="txt_" + body[:8])
    curve.body = body
    curve.align_x = align
    curve.size = size
    obj = bpy.data.objects.new("label_" + body[:8], curve)
    obj.location = location
    obj.rotation_euler = rotation
    bpy.context.collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def fmt_usd(v):
    return f"{v/1000:.0f}K USD" if v >= 1000 else f"{v} USD"


def fmt_jpy(v):
    return f"{v/1_000_000:.0f}M JPY"


# =========================================================================
# 3. シーン構築
# =========================================================================
def build():
    clear_scene()

    n = len(THEMES)
    gap = CONFIG["bar_gap"]
    total_span = (n - 1) * gap
    x0 = -total_span / 2.0

    label_mat = make_material("label_dark", (0.05, 0.05, 0.07, 1.0), roughness=0.6)

    max_h = 0.0
    for i, (tid, name, cat, invest, cont, cd, deps) in enumerate(THEMES):
        x = x0 + i * gap
        height = max(cd * CONFIG["height_scale"], 0.05)
        width = max(math.sqrt(invest) * CONFIG["width_scale"], CONFIG["min_width"])
        max_h = max(max_h, height)

        color = CATEGORY_COLORS.get(cat, (0.6, 0.6, 0.6, 1.0))
        # 基盤で単一障害点の T1 はわずかに発光させて主役感を出す
        emission = 0.6 if tid == "T1" else 0.0
        mat = make_material(f"mat_{tid}", color, roughness=0.35, emission=emission)

        add_box(f"bar_{tid}", width, height, (x, 0.0, 0.0), mat)

        if CONFIG["show_labels"]:
            # 柱の上に CD 値、足元にテーマ ID を置く
            add_text(fmt_usd(cd), (x, 0.0, height + 0.25),
                     size=0.26, material=label_mat)
            add_text(f"{tid}", (x, -width * 0.5 - 0.15, 0.02),
                     size=0.30, rotation=(0, 0, 0), material=label_mat)
            add_text(fmt_jpy(invest), (x, -width * 0.5 - 0.55, 0.02),
                     size=0.18, rotation=(0, 0, 0), material=label_mat)

    # --- 地面 ---
    ground_mat = make_material("ground", (0.86, 0.87, 0.90, 1.0), roughness=0.9)
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
    ground = bpy.context.object
    ground.name = "ground"
    ground.scale = (total_span + CONFIG["ground_margin"] * 2,
                    CONFIG["ground_margin"] * 2, 1.0)
    ground.data.materials.append(ground_mat)

    return max_h, total_span


def setup_camera_and_light(max_h, span):
    """斜め俯瞰のカメラ + 3点ライティング + 環境光。"""
    # --- カメラ ---
    cam_data = bpy.data.cameras.new("Camera")
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    dist = span * 1.05 + 6.0
    cam.location = (span * 0.55 + 4.0, -(dist), max_h * 0.9 + 4.0)
    # ターゲット（原点やや上）を見るよう Track To
    target = bpy.data.objects.new("cam_target", None)
    target.location = (0, 0, max_h * 0.4)
    bpy.context.collection.objects.link(target)
    con = cam.constraints.new(type="TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    cam_data.lens = 45
    bpy.context.scene.camera = cam

    # --- キーライト（太陽光） ---
    key = bpy.data.lights.new("Key", type="SUN")
    key.energy = 3.2
    key_obj = bpy.data.objects.new("Key", key)
    key_obj.location = (span, -span, max_h + 10)
    key_obj.rotation_euler = (math.radians(55), math.radians(15), math.radians(35))
    bpy.context.collection.objects.link(key_obj)

    # --- フィルライト（面光源） ---
    fill = bpy.data.lights.new("Fill", type="AREA")
    fill.energy = 400
    fill.size = 12
    fill_obj = bpy.data.objects.new("Fill", fill)
    fill_obj.location = (-span, -span * 0.5, max_h + 6)
    fill_obj.rotation_euler = (math.radians(60), 0, math.radians(-30))
    bpy.context.collection.objects.link(fill_obj)

    # --- 環境光（薄い空色） ---
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.92, 0.94, 0.98, 1.0)
        bg.inputs["Strength"].default_value = 0.7


def setup_render():
    scene = bpy.context.scene
    w, h = CONFIG["resolution"]
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False

    engine = CONFIG["render_engine"].upper()
    if engine == "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = CONFIG["samples"]
    else:
        # Blender 4.2+ は "BLENDER_EEVEE_NEXT"、それ以前は "BLENDER_EEVEE"
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        except TypeError:
            scene.render.engine = "BLENDER_EEVEE"
        # EEVEE のサンプル設定（属性名がバージョンで異なる）
        try:
            scene.eevee.taa_render_samples = CONFIG["samples"]
        except Exception:
            pass

    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    out_dir = os.path.join(here, "out")
    os.makedirs(out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(out_dir, CONFIG["out_name"])
    scene.render.image_settings.file_format = "PNG"
    return scene.render.filepath


# =========================================================================
# 4. エントリポイント
# =========================================================================
def main():
    argv = sys.argv
    no_render = "--no-render" in argv

    max_h, span = build()
    setup_camera_and_light(max_h, span)
    out_path = setup_render()

    if not no_render:
        print(f"[roi_portfolio_render] レンダリング開始 → {out_path}")
        bpy.ops.render.render(write_still=True)
        print(f"[roi_portfolio_render] 完了: {out_path}")
    else:
        print("[roi_portfolio_render] --no-render 指定のため組み立てのみ実行")


if __name__ == "__main__":
    main()

# -------------------------------------------------------------------------
# NOTE（日本語ラベルについて）:
#   Blender の既定フォントは日本語グリフを持たず「豆腐(□)」になります。
#   日本語表示が必要なら、各 add_text 前後で下記のように日本語フォントを
#   割り当ててください（例: Noto Sans CJK JP）:
#
#       font = bpy.data.fonts.load("/path/to/NotoSansCJKjp-Regular.otf")
#       curve.font = font   # add_text 内の curve に設定
#
#   ローマ字ラベル（T1..T7 / 171K USD / 90M JPY 等）は既定フォントで問題なく出ます。
# -------------------------------------------------------------------------
