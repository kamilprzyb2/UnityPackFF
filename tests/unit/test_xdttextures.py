"""
FusionFall's texture swaps: unitypack/xdttextures.py (texture_swaps,
Swap.texture_for, prefab_key) on a made-up XDT, as plain dicts shaped like
the read xdtdatas object.

Each table pairs data rows with mesh rows through m_iMesh; a mesh row names
the prefab's model and up to two textures, "null" for none.
"""

from unitypack.xdttextures import Swap, prefab_key, texture_swaps


def mesh(model, tex1="null", tex2="null"):
	return {"m_pstrMMeshModelString": model, "m_pstrMTextureString": tex1, "m_pstrMTextureString2": tex2}


EMPTY = mesh("")

XDT = {
	"m_pNanoTable": {
		"m_pNanoData": [{"m_iMesh": 0}, {"m_iMesh": 1}, {"m_iMesh": 2}],
		"m_pNanoMeshData": [EMPTY, mesh("nano_eduardo", "nano_eduardo", "nano_eduardo_face"), mesh("nano_billy", "nano_billy")],
	},
	"m_pNpcTable": {
		"m_pNpcData": [
			{"m_iMesh": 1, "m_iNpcType": 1, "m_iHNpc": 0},
			{"m_iMesh": 2, "m_iNpcType": 100, "m_iHNpc": 0},  # an invisible marker: keeps its own
			{"m_iMesh": 3, "m_iNpcType": 1, "m_iHNpc": 1},  # a human NPC: built from player parts
			{"m_iMesh": 4, "m_iNpcType": 2, "m_iHNpc": 0},
			{"m_iMesh": 5, "m_iNpcType": 2, "m_iHNpc": 0},
			{"m_iMesh": 4, "m_iNpcType": 2, "m_iHNpc": 0},  # the same set again: listed once
			{"m_iMesh": 9, "m_iNpcType": 2, "m_iHNpc": 0},  # no such mesh row
		],
		"m_pNpcMeshData": [
			EMPTY, mesh("Mob_Bear", "mob_bear"), mesh("mob_marker", "mob_marker"), mesh("mob_human", "npc_x"),
			mesh("DT_Car", "DT_car_01"), mesh("DT_Car", "DT_car_02"),
		],
	},
	"m_pShinyTable": {"m_pShinyData": [{"m_iMesh": 1}], "m_pShinyMeshData": [EMPTY, mesh("shiny_egg", "egg_a", "egg_b")]},
	"m_pTransportationTable": {"m_pTransportationData": [{"m_iMesh": 0}], "m_pTransportationMeshData": [mesh("bus", "bus_tex")]},
	"m_pVehicleItemTable": {
		"m_pItemData": [{"m_iMesh": 1}, {"m_iMesh": 2}, {"m_iMesh": 3}],
		"m_pItemMeshData": [EMPTY, mesh("vehicle_board", "board_red"), mesh("vehicle_board", "board_blue"), mesh("vehicle_none")],
	},
}


def test_texture_swaps():
	s = texture_swaps(XDT)
	assert s[("nano", "nano_eduardo")] == [Swap("nano_eduardo", "nano_eduardo_face", False)]
	assert s[("nano", "nano_billy")] == [Swap("nano_billy", None, False)]
	assert s[("mob", "mob_bear")] == [Swap("mob_bear", None, False)]
	assert s[("mob", "dt_car")] == [Swap("DT_car_01", None, False), Swap("DT_car_02", None, False)]
	assert s[("mob", "shiny_egg")] == [Swap("egg_a", "egg_b", False)]
	assert s[("mob", "bus")] == [Swap("bus_tex", None, False)]
	assert s[("wear", "vehicle_board")] == [Swap("board_red", None, True), Swap("board_blue", None, True)]


def test_rows_that_keep_their_own_textures():
	s = texture_swaps(XDT)
	assert ("mob", "mob_marker") not in s  # NPC type 100 and up
	assert ("mob", "mob_human") not in s  # human NPC
	assert ("wear", "vehicle_none") not in s  # names no texture
	assert ("nano", "") not in s


def test_missing_tables():
	assert texture_swaps({}) == {}
	assert texture_swaps({"m_pNanoTable": None}) == {}


def test_main_and_sub():
	swap = Swap("body", "face", False)
	assert swap.texture_for("nano_x-main-link_a.dds", 0) == "body"
	assert swap.texture_for("nano_x-sub-link_b.dds", 0) == "face"
	assert swap.texture_for("Nano_X-SUB-link_b (Instance)", 0) == "face"
	assert swap.texture_for("nano_x-eyes", 0) is None
	# only a renderer's first material
	assert swap.texture_for("nano_x-main-link_a.dds", 1) is None


def test_sub_without_a_second_texture():
	# "sub" materials keep their own; a name with both takes the second texture's rule first
	swap = Swap("body", None, False)
	assert swap.texture_for("x-sub", 0) is None
	assert swap.texture_for("x-main", 0) == "body"
	assert Swap("body", "face", False).texture_for("main-sub", 0) == "face"


def test_vehicles_use_the_first_texture_on_both():
	swap = Swap("board_red", None, True)
	assert swap.texture_for("vehicle_board-main", 0) == "board_red"
	assert swap.texture_for("vehicle_board-sub", 0) == "board_red"
	assert swap.texture_for("vehicle_board-glass", 0) is None


def test_prefab_key():
	assert prefab_key("nano/nano_eduardo.kfm") == ("nano", "nano_eduardo")
	assert prefab_key("Mob/DT_Car.kfm") == ("mob", "dt_car")
	assert prefab_key("wear/vehicle_board.nif") == ("wear", "vehicle_board")
	assert prefab_key("nano/nano_blilly.nif") is None
	assert prefab_key("wear/hat_x.kfm") is None
	assert prefab_key("texture/x.dds") is None
