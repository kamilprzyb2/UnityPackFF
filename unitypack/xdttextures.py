"""
FusionFall's texture swaps. When the client spawns a nano, an NPC or mob, a
shiny, the bus, a vehicle or a player's clothes, it replaces the main
texture of the prefab's materials with textures the XDT names (loaded as
"texture/<name>.dds"). The prefab's own textures are placeholders: some
point at the wrong texture (Eduardo's face), some at none (Bubbles, many
shirts).

Three rules (Swap.rule):
- FIRST (nanos, NPCs and mobs, shinies, the bus; hats, glasses, weapons and
  back items hung on the body): on each renderer's first material only, a
  material whose name contains "sub" takes the second texture, if there is
  one; otherwise one whose name contains "main" takes the first.
- VEHICLE: the same, but "main" and "sub" both take the first texture.
- WORN (clothes, capes, faces, hair: what the client skins into the
  character): every material whose name contains "sub" takes the second
  texture, every other material the first. For clothes and faces the
  second texture is the skin (m_skin or f_skin).

Items name a model and textures for boys (m_pstrM...) and girls
(m_pstrF...). Faces and hair are models "<name>_type01" and "_type02"
(the second for under some hats); a face's texture depends on the eye
colour ("<name>_a" to "_e"), hair is "<name>_a" (the game tints it with the
hair colour).

NPCs of type 100 and up and human NPCs (built from player parts) keep the
prefab's own textures. A prefab can be used with several sets of textures
(car colours, hoverboard designs, a face's eye colours): texture_swaps()
lists each distinct set once, in table order.
"""

from collections import namedtuple

NULL = "null"
MAIN, SUB = "main", "sub"
FIRST, VEHICLE, WORN = "first", "vehicle", "worn"
EYE_COLORS = "abcde"
# item tables: XDT table -> rule for their models
CLOTHES = ("m_pShirtsItemTable", "m_pPantsItemTable", "m_pShoesItemTable")
ATTACHED = ("m_pHatItemTable", "m_pGlassItemTable", "m_pWeaponItemTable")


def _name(value):
	"A texture or model name from the XDT, or None for an empty one"
	return None if not value or value.lower() == NULL else value


class Swap(namedtuple("Swap", "main sub rule")):
	"The textures (first and second, None for none) the game puts on a prefab, and its rule"

	def texture_for(self, material_name, slot):
		"The texture name to use on a renderer's material slot, or None to keep its own"
		name = material_name.lower()
		if self.rule == WORN:
			return (self.sub if SUB in name else self.main) or None
		if slot != 0:
			return None
		if self.rule == VEHICLE:
			return self.main if MAIN in name or SUB in name else None
		if SUB in name and self.sub:
			return self.sub
		if MAIN in name and self.main:
			return self.main
		return None


def prefab_key(container_path):
	"""
	The (folder, model) a container path loads, as the client builds it:
	"nano/<model>.kfm" for nanos, "mob/<model>.kfm" for NPCs, mobs, shinies
	and the bus, "wear/<model>.nif" for vehicles; None for anything else
	"""
	path = container_path.lower()
	for folder, ext in (("nano/", ".kfm"), ("mob/", ".kfm"), ("wear/", ".nif")):
		if path.startswith(folder) and path.endswith(ext):
			return folder[:-1], path[len(folder):-len(ext)]
	return None


def texture_swaps(xdt):
	"{(folder, model): [Swap, ...]} for every prefab the XDT re-textures; xdt is the read xdtdatas object"
	swaps = {}

	def put(folder, model, swap):
		if model is None or (swap.main is None and swap.sub is None):
			return
		found = swaps.setdefault((folder, model.lower()), [])
		if swap not in found:
			found.append(swap)

	def add(folder, mesh, rule=FIRST):
		put(folder, _name(mesh["m_pstrMMeshModelString"]),
			Swap(_name(mesh["m_pstrMTextureString"]), _name(mesh["m_pstrMTextureString2"]), rule))

	def genders(mesh):
		"(model, texture, texture 2, skin) for the boy's and the girl's model"
		for g, skin in (("M", "m_skin"), ("F", "f_skin")):
			yield (_name(mesh["m_pstr%sMeshModelString" % g]), _name(mesh["m_pstr%sTextureString" % g]),
				_name(mesh["m_pstr%sTextureString2" % g]), skin)

	def rows(table, data, meshes):
		t = xdt.get(table) or {}
		mesh_rows = t.get(meshes) or []
		for row in t.get(data) or []:
			i = row["m_iMesh"]
			if 0 <= i < len(mesh_rows):
				yield row, mesh_rows[i]

	for row, mesh in rows("m_pNanoTable", "m_pNanoData", "m_pNanoMeshData"):
		add("nano", mesh)
	for row, mesh in rows("m_pNpcTable", "m_pNpcData", "m_pNpcMeshData"):
		if row.get("m_iHNpc") != 1 and row["m_iNpcType"] < 100:
			add("mob", mesh)
	for row, mesh in rows("m_pShinyTable", "m_pShinyData", "m_pShinyMeshData"):
		add("mob", mesh)
	for row, mesh in rows("m_pTransportationTable", "m_pTransportationData", "m_pTransportationMeshData"):
		add("mob", mesh)
	for row, mesh in rows("m_pVehicleItemTable", "m_pItemData", "m_pItemMeshData"):
		add("wear", mesh, VEHICLE)

	# what a player wears: the client's SetBody, SetBackFack, AttachGO, SetFace, SetHead
	for table in CLOTHES:
		for row, mesh in rows(table, "m_pItemData", "m_pItemMeshData"):
			for model, tex, tex2, skin in genders(mesh):
				put("wear", model, Swap(tex, skin, WORN))
	for row, mesh in rows("m_pBackItemTable", "m_pItemData", "m_pItemMeshData"):
		cape = row.get("m_iEquipType") == 1
		for model, tex, tex2, skin in genders(mesh):
			put("wear", model, Swap(tex, tex2, WORN if cape else FIRST))
	for table in ATTACHED:
		for row, mesh in rows(table, "m_pItemData", "m_pItemMeshData"):
			for model, tex, tex2, skin in genders(mesh):
				put("wear", model, Swap(tex, tex2, FIRST))
	for row, mesh in rows("m_pFaceItemTable", "m_pItemData", "m_pItemMeshData"):
		for model, tex, tex2, skin in genders(mesh):
			for cut in ("_type01", "_type02"):
				for eye in EYE_COLORS:
					put("wear", model and model + cut, Swap(tex and "%s_%s" % (tex, eye), skin, WORN))
	for row, mesh in rows("m_pHeadItemTable", "m_pItemData", "m_pItemMeshData"):
		for model, tex, tex2, skin in genders(mesh):
			for cut in ("_type01", "_type02"):
				put("wear", model and model + cut, Swap(tex and tex + "_a", None, WORN))
	return swaps
