"""
FusionFall's texture swaps. When the client spawns a nano, an NPC or mob, a
shiny, the bus or a vehicle, it replaces the main texture of the prefab's
materials with the textures the object's XDT mesh row names
(m_pstrMTextureString and m_pstrMTextureString2, loaded as
"texture/<name>.dds"). The prefab's own textures are placeholders: some
point at the wrong texture (Eduardo's face), some at none (Bubbles).

The rule, for each renderer, on its first material only:
- a material whose name contains "sub" takes the second texture, if the row
  names one;
- otherwise, one whose name contains "main" takes the first texture.
Vehicles put the first texture on both "main" and "sub" materials.

NPCs of type 100 and up and human NPCs (built from player parts) keep the
prefab's own textures. A prefab can be used by several rows with different
textures (car colours, hoverboard designs): texture_swaps() lists each
distinct set once, in table order.
"""

from collections import namedtuple

NULL = "null"
MAIN, SUB = "main", "sub"


def _name(value):
	"A texture or model name from the XDT, or None for an empty one"
	return None if not value or value.lower() == NULL else value


class Swap(namedtuple("Swap", "main sub vehicle")):
	"The textures one XDT row puts on a prefab"

	def texture_for(self, material_name, slot):
		"The texture name to use on a renderer's material slot, or None to keep its own"
		if slot != 0:
			return None
		name = material_name.lower()
		if self.vehicle:
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

	def add(folder, mesh, vehicle=False):
		model = _name(mesh["m_pstrMMeshModelString"])
		if model is None:
			return
		swap = Swap(_name(mesh["m_pstrMTextureString"]), _name(mesh["m_pstrMTextureString2"]), vehicle)
		if swap.main is None and swap.sub is None:
			return
		found = swaps.setdefault((folder, model.lower()), [])
		if swap not in found:
			found.append(swap)

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
		add("wear", mesh, vehicle=True)
	return swaps
