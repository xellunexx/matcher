def _fp_from_value(fp, notes, what):
    """Contract polygon value {polygon_m, holes_m, state, source} -> (poly, state, source)."""
    if not isinstance(fp, dict):
        return None, None, ""
    shell = fp.get("polygon_m")
    if not isinstance(shell, list) or len(shell) < 3:
        return None, None, ""
    p = _mkpoly([[_r6(q[0]), _r6(q[1])] for q in shell],
                [[[_r6(q[0]), _r6(q[1])] for q in h]
                 for h in (fp.get("holes_m") or []) if isinstance(h, list) and len(h) >= 3],
                notes, what)
    st = fp.get("state") if fp.get("state") in _STATE_RANK else "UNKNOWN"
    return p, st, str(fp.get("source") or "")


def _letters(tag):
    """Building ordinal -> (Cyrillic, Latin) display letters ("А", "A"); digits past J."""
    if isinstance(tag, int) and 1 <= tag <= len(_CYR_ORD):
        return _CYR_ORD[tag - 1], _LAT_ORD[tag - 1]
    return str(tag), str(tag)


def _building_specs(ev, rd, notes):
    """One spec per building. Fused evidence["buildings"] (each with ITS OWN
    footprint/dimensions/roof) is authoritative; legacy evidence (footprint +
    extra_footprints) yields a primary spec plus secondary specs that inherit the
    primary's storeys/floor height/roof as INFERRED (never re-observed)."""
    specs = []
    blds = ev.get("buildings")
    if isinstance(blds, list) and any(isinstance(b, dict) for b in blds):
        for bi, b in enumerate([x for x in blds if isinstance(x, dict)], start=1):
            bid = f"bldg-{bi}"
            poly, fp_state, src = _fp_from_value(b.get("footprint"), notes, f"{bid} footprint")
            if poly is None:
                notes.append(f"{bid}: footprint polygon unusable; building omitted")
                continue
            mini = {"dimensions": b.get("dimensions") or {}, "roof": b.get("roof")}
            sub = []
            storeys, st_state, st_refs = _resolve_storeys(mini, sub)
            fh, fh_state, fh_refs = _resolve_floor_height(mini, storeys, sub)
            notes.extend(f"{bid}: {n}" for n in sub)
            tag = b.get("tag") if isinstance(b.get("tag"), int) else bi
            specs.append({
                "bid": bid, "tag": tag,
                "tag_state": b.get("tag_state") if b.get("tag_state") in _STATE_RANK else "INFERRED",
                "tag_basis": str(b.get("tag_basis") or ""),
                "poly": poly, "fp_state": fp_state,
                "fp_refs": [(src, str(b.get("footprint_locator") or "footprint"))],
                "srcs": [src] if src else [],
                "storeys": storeys, "st_state": st_state, "st_refs": st_refs,
                "fh": fh, "fh_state": fh_state, "fh_refs": fh_refs,
                "roof": _roof_fact(mini), "h_obs": _dim(mini, "height_m"),
                "openings": _openings_facts(ev) if bi == 1 else [],
                "inherited": False,
            })
        if specs:
            return specs
    poly, fp_state, fp_refs, srcs = _resolve_footprint(ev, rd, notes)
    if poly is None:
        return []
    storeys, st_state, st_refs = _resolve_storeys(ev, notes)
    fh, fh_state, fh_refs = _resolve_floor_height(ev, storeys, notes)
    specs.append({"bid": "bldg-1", "tag": 1, "tag_state": "INFERRED", "tag_basis": "primary",
                  "poly": poly, "fp_state": fp_state, "fp_refs": fp_refs, "srcs": srcs,
                  "storeys": storeys, "st_state": st_state, "st_refs": st_refs,
                  "fh": fh, "fh_state": fh_state, "fh_refs": fh_refs,
                  "roof": _roof_fact(ev), "h_obs": _dim(ev, "height_m"),
                  "openings": _openings_facts(ev), "inherited": False})
    extras = ev.get("extra_footprints")
    if isinstance(extras, list):
        for k, ef in enumerate(extras):
            bi = len(specs) + 1
            bid = f"bldg-{bi}"
            ep, _st, _src = _fp_from_value(ef, notes, f"extra_footprint {bid}")
            if ep is None:
                continue
            src0 = fp_refs[0][0] if fp_refs else "dxf"
            specs.append({"bid": bid, "tag": bi, "tag_state": "INFERRED", "tag_basis": "drawing order",
                          "poly": ep, "fp_state": _worst(fp_state or "OBSERVED", "INFERRED"),
                          "fp_refs": [(src0, f"extra_footprints[{k}]")], "srcs": [src0] if src0 else [],
                          "storeys": storeys, "st_state": "INFERRED", "st_refs": [],
                          "fh": fh, "fh_state": "INFERRED", "fh_refs": [],
                          "roof": _roof_fact(ev), "h_obs": None, "openings": [], "inherited": True})
            notes.append(f"{bid}: secondary stack from extra_footprints "
                         f"(storeys/height inherited INFERRED)")
    return specs


# ── roof decomposition: rectilinear footprints -> rectangular wings ────────────
def _is_rectilinear(poly, tol=1e-6):
    c = list(poly.exterior.coords)
    return all(abs(x0 - x1) <= tol or abs(y0 - y1) <= tol
               for (x0, y0), (x1, y1) in zip(c, c[1:]))


def _largest_inner_rect(poly, cap=400):
    """Largest axis-aligned rectangle spanned by the polygon's own vertex grid that
    lies inside it (deterministic: grid order, lexicographic tie-break)."""
    xs = sorted({_r6(x) for x, _ in poly.exterior.coords})
    ys = sorted({_r6(y) for _, y in poly.exterior.coords})
    host = poly.buffer(1e-7)
    best, n = None, 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            for k in range(len(ys)):
                for m in range(k + 1, len(ys)):
                    n += 1
                    if n > cap:
                        return best
                    r = Polygon([(xs[i], ys[k]), (xs[j], ys[k]), (xs[j], ys[m]), (xs[i], ys[m])])
                    if host.contains(r) and (best is None or r.area > best.area + 1e-9):
                        best = r
    return best


def _rect_wings(poly, notes, what):
    """Greedy decomposition of a rectilinear hole-free footprint into <= 6
    rectangles (largest first). Returns (wings, decomposed); non-rectilinear or
    holed footprints stay one wing (single ridge, as before)."""
    try:
        simple = poly.simplify(1e-6)
        if simple.geom_type != "Polygon" or simple.interiors or not _is_rectilinear(simple):
            return [poly], False
        pieces, wings = [simple], []
        while pieces and len(wings) < 6:
            pieces.sort(key=lambda g: (-g.area, g.bounds))
            cur = pieces.pop(0)
            rect = _largest_inner_rect(cur)
            if rect is None:
                return [poly], False
            wings.append(rect)
            rest = cur.difference(rect).simplify(1e-6)
            parts = list(rest.geoms) if rest.geom_type == "MultiPolygon" else (
                [rest] if rest.geom_type == "Polygon" else [])
            pieces.extend(g for g in parts if g.area >= 0.5 and _is_rectilinear(g))
            if any(not _is_rectilinear(g) for g in parts if g.area >= 0.5):
                return [poly], False
        if pieces:
            notes.append(f"{what}: footprint too fragmented for wing roofs; single ridge")
            return [poly], False
        if len(wings) == 1:
            return wings, False
        return wings, True
    except Exception as ex:
        notes.append(f"{what}: wing decomposition failed ({ex}); single ridge")
        return [poly], False


def _shared_edge_axis(wing, placed):
    """Ridge axis of an attached wing: perpendicular to its longest edge shared
    with already placed wings ("x" when that edge runs along Y)."""
    best_len, axis = 0.0, None
    for p in placed:
        try:
            inter = wing.exterior.intersection(p.exterior)
        except Exception:
            continue
        geoms = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
        for g in geoms:
            if g.geom_type != "LineString" or g.length <= best_len + 1e-9:
                continue
            (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
            best_len = g.length
            axis = "x" if abs(x0 - x1) <= abs(y0 - y1) else "y"
    return axis


def _compile_building(spec, ctx, objects, register):
    """Emit one building's object stack (root envelope, shells or earthworks →
    foundation → slabs/walls, roof, openings, BOQ-proven bands)."""
    bid, poly, reno, multi = spec["bid"], spec["poly"], ctx["reno"], ctx["multi"]
    crows, notes = ctx["crows"], ctx["notes"]
    storeys, fh = spec["storeys"], spec["fh"]
    fp_state, fp_refs = spec["fp_state"], spec["fp_refs"]
    cyr, lat = _letters(spec["tag"])
    shell_h = FOUNDATION_H + storeys * fh
    core_state = _worst(fp_state or "UNKNOWN", spec["fh_state"], spec["st_state"])
    inh = "INFERRED" if spec["inherited"] else "OBSERVED"
    minx, miny, maxx, maxy = poly.bounds
    dx, dy = maxx - minx, maxy - miny
    brefs = _refs(*(fp_refs + spec["fh_refs"] + spec["st_refs"]))

    def lab(bg, en):
        return f"{cyr}: {bg} · {lat}: {en}" if multi else f"{bg} · {en}"

    def emit(oid, bg, en, sem, op, phase, geometry, state, refs, cat, qty, unit, desc):
        o = _obj(oid, lab(bg, en), sem, op, phase, geometry, state, refs, [], qty, unit, desc,
                 parent=bid)
        objects.append(o)
        register(bid, cat, oid)
        return o

    root_op, root_phase = ("EXISTING", "existing") if reno else ("CONSTRUCT", "structure")
    root_desc = ("Сграден обвивочен блок (bbox на контура) · building envelope box (footprint bounds)")
    if multi:
        root_desc += f"; идентичност {spec['tag_state']}: {spec['tag_basis']} · identity"
    root = _obj(bid, lab("Сграда", "building") if not multi else f"Сграда {cyr} · building {lat}",
                "BUILDING", root_op, root_phase,
                {"type": "box", "center_m": [_r6((minx + maxx) / 2), _r6((miny + maxy) / 2), _r6(shell_h / 2)],
                 "size_m": [_r6(dx), _r6(dy), _r6(shell_h)]},
                core_state, brefs, [], None, None, root_desc, parent=None)
    root["inspect"]["building_tag"] = spec["tag"]
    root["inspect"]["tag_state"] = spec["tag_state"]
    objects.append(root)
    register(bid, "root", bid)

    if reno:
        shells = [  # (slug, op, phase, semantic, inner_in, outer_in, category)
            ("repair.facade", "REPAIR", "facade", "WALL_EXT", 0.00, 0.10, "repair"),
            ("insulate.facade", "INSULATE", "facade", "WALL_EXT", 0.10, 0.25, "insulate"),
            ("paint.int", "PAINT", "finishes", "WALL_INT", 0.25, 0.35, "paint"),
        ]
        for slug, op, phase, sem, i0, i1, cat in shells:
            band = _ring(poly, i1, notes, f"{bid} {slug}")
            if band is not None and i0 > 0:
                try:
                    outer = poly.buffer(-i0)
                    band = band.intersection(outer) if not outer.is_empty else None
                    if band is not None and band.geom_type == "MultiPolygon":
                        band = max(band.geoms, key=lambda g: g.area)
                    if band is not None and (band.is_empty or band.geom_type != "Polygon"):
                        band = None
                except Exception:
                    band = None
            if band is None:
                notes.append(f"{bid} {slug}: shell collapsed on narrow footprint; object omitted")
                continue
            bg, en = {"repair.facade": ("Ремонт на фасада", "facade repair"),
                      "insulate.facade": ("Топлоизолация", "facade insulation"),
                      "paint.int": ("Боядисване", "interior paint")}[slug]
            emit(f"{bid}.{slug}.l0", bg, en, sem, op, phase, _geom_of(band, FOUNDATION_H, shell_h),
                 _worst(core_state, "INFERRED"), _refs(*(fp_refs + [("pack.boq", f"vocab:{cat}")])),
                 cat, None, None,
                 {"repair.facade": "Ремонтен слой 0–10 см · repair shell 0-0.10 m",
                  "insulate.facade": "Изолационен слой 10–25 см · insulation shell 0.10-0.25 m",
                  "paint.int": "Вътрешен довършителен слой 25–35 см · paint shell 0.25-0.35 m"}[slug])
    else:
        tring = _ring(poly, TRENCH_W, notes, f"{bid} trench")
        if tring is not None:
            emit(f"{bid}.trench.l0", "Изкоп за фундаменти", "foundation trench", "TRENCH", "EXCAVATE",
                 "earthworks", _geom_of(tring, -TRENCH_D, GROUND), _worst(fp_state, "INFERRED"),
                 _refs(*(fp_refs + [("rule", "trench 0.6 m around foundation")])), "trench",
                 tring.area * TRENCH_D, "m3",
                 "Трапецовиден изкоп −0.60…0.00 м · excavation band below grade (EXCAVATE may sit below 0)")
        emit(f"{bid}.foundation.l0", "Фундамент", "foundation", "FOUNDATION", "CONSTRUCT", "foundation",
             _geom_of(poly, GROUND, FOUNDATION_H), fp_state, _refs(*fp_refs), "foundation",
             poly.area * FOUNDATION_H, "m3", "Лента/плоча 0.00…0.60 м · foundation extruded to 0.60 m")
        for i in range(1, storeys + 1):
            zi = FOUNDATION_H + (i - 1) * fh
            emit(f"{bid}.slab.l{i}", f"Плоча, етаж {i}", f"slab storey {i}", "SLAB", "CONSTRUCT",
                 "structure", _geom_of(poly, zi, zi + SLAB_T), _worst(core_state, inh), brefs, "slab",
                 poly.area * SLAB_T, "m3",
                 f"Междуетажна плоча {zi:.2f}…{zi + SLAB_T:.2f} м · slab, net area*0.30 (holes kept)")
            wring = _ring(poly, WALL_T, notes, f"{bid} wall l{i}")
            if wring is not None:
                emit(f"{bid}.wall-ext.l{i}", f"Външна стена, етаж {i}", f"exterior wall storey {i}",
                     "WALL_EXT", "CONSTRUCT", "envelope", _geom_of(wring, zi + SLAB_T, zi + fh),
                     _worst(core_state, inh), brefs, "wall",
                     wring.exterior.length * (fh - SLAB_T) * WALL_T, "m3",
                     f"Стенен пръстен 0.25 м, {zi + SLAB_T:.2f}…{zi + fh:.2f} м · ring = poly−buffer(−0.25)")

    roof = spec["roof"]
    roof_type = roof["type"] if roof else "unknown"
    roof_state = roof["state"] if roof else "UNKNOWN"
    roof_op = "EXISTING" if reno else "CONSTRUCT"
    roof_refs = _refs(*(fp_refs + ([(roof["source"], "roof")] if roof and roof["source"] else [])))
    if roof_type == "gable":
        eave = shell_h
        wings, decomposed = _rect_wings(poly, notes, f"{bid} roof")
        main = wings[0]
        mnx, mny, mxx, mxy = main.bounds
        mdx, mdy = mxx - mnx, mxy - mny
        pitch = roof["pitch_deg"]
        h_obs = spec["h_obs"]
        if pitch is not None and pitch > 0:
            tan_p, basis = math.tan(math.radians(pitch)), f"наблюдаван наклон {pitch:g}° · observed pitch"
        elif h_obs is not None and h_obs[0] > eave and min(mdx, mdy) > 0:
            tan_p = (h_obs[0] - eave) / (0.5 * min(mdx, mdy))
            basis = "от наблюдавана обща височина · from observed total height"
        else:
            tan_p, basis = math.tan(math.radians(DEFAULT_PITCH_DEG)), f"наклон по подразбиране {DEFAULT_PITCH_DEG:g}° (INFERRED) · default pitch"
        placed = []
        for wi, wing in enumerate(wings):
            wnx, wny, wxx, wxy = wing.bounds
            wdx, wdy = wxx - wnx, wxy - wny
            if wi == 0:
                axis = "x" if wdx >= wdy else "y"
            else:
                axis = _shared_edge_axis(wing, placed) or ("x" if wdx >= wdy else "y")
            span = wdy if axis == "x" else wdx
            rise = 0.5 * span * tan_p
            oid = f"{bid}.roof.l0" if wi == 0 else f"{bid}.roof.l0.n{wi}"
            bg = "Двускатен покрив" if wi == 0 else f"Покривно крило {wi}"
            en = "gable roof" if wi == 0 else f"roof wing {wi}"
            emit(oid, bg, en, "ROOF", roof_op, "roof",
                 {"type": "gable_roof",
                  "polygon_m": [[_r6(x), _r6(y)] for x, y in wing.exterior.coords[:-1]],
                  "eave_z_m": _r6(eave), "ridge_z_m": _r6(eave + rise), "ridge_axis": axis},
                 _worst(roof_state, core_state, inh), roof_refs, "roof", wing.area, "m2",
                 f"Двускатен покрив, гребен по {axis.upper()}; {basis}"
                 + ("; крило от правоъгълна декомпозиция на контура · wing of rectilinear decomposition"
                    if decomposed else ""))
            placed.append(wing)
    elif not reno or roof is not None:
        if roof is None:
            notes.append(f"{bid}: roof type unknown: flat roof compiled, state INFERRED (per contract)")
        flat_state = _worst(roof_state, "INFERRED") if roof is None or roof_state == "UNKNOWN" else roof_state
        emit(f"{bid}.roof.l0", "Плосък покрив", "flat roof", "ROOF", roof_op, "roof",
             _geom_of(poly, shell_h, shell_h + ROOF_T), _worst(flat_state, core_state, inh), roof_refs,
             "roof", poly.area, "m2", "Плосък покрив, дебелина 0.30 м · flat roof slab on stack top")

    openings = spec["openings"]
    if openings:
        ext_len = poly.exterior.length
        per_lvl = {}
        for idx, o in enumerate(openings):
            lvl = o["level"] if o["level"] else 1 + (idx % storeys)
            lvl = min(max(1, lvl), storeys)
            per_lvl.setdefault(lvl, []).append(o)
        for lvl in sorted(per_lvl):
            group = per_lvl[lvl]
            zi = FOUNDATION_H + (lvl - 1) * fh
            for j, o in enumerate(group):
                frac = (j + 1) / (len(group) + 1)
                pt = poly.exterior.interpolate(frac * ext_len)
                sem = "OPENING_DOOR" if o["type"] == "door" else "OPENING_WINDOW"
                cz = zi + o["sill_m"] + o["height_m"] / 2
                emit(f"{bid}.opening-{o['type']}.l{lvl}.n{j}",
                     f"{'Врата' if o['type'] == 'door' else 'Прозорец'} ет.{lvl}", f"{o['type']} level {lvl}",
                     sem, "REPLACE" if reno else "INSTALL", "openings",
                     {"type": "box", "center_m": [_r6(pt.x), _r6(pt.y), _r6(cz)],
                      "size_m": [_r6(o["width_m"]), 0.30, _r6(o["height_m"])]},
                     _worst(o["state"], "INFERRED"), _refs((o["source"], f"openings[{j}]")), "openings",
                     None, None, f"{o['type']} {o['width_m']}×{o['height_m']} м, разположение по контура (INFERRED)")

    if not reno:
        # BOQ-driven envelope layers: ONLY when rows prove scope for THIS building
        ins_band = None
        if _has_rows(crows, bid, "insulate"):
            ins_band = _outring(poly, 0.12, notes, f"{bid} insulate band")
            if ins_band is not None:
                emit(f"{bid}.insulate.l0", "Топлоизолация", "facade insulation", "WALL_EXT", "INSULATE",
                     "facade", _geom_of(ins_band, FOUNDATION_H + SLAB_T, shell_h),
                     _worst(core_state, "INFERRED"), _refs(*(fp_refs + [("pack.boq", "vocab:insulate")])),
                     "insulate", ins_band.exterior.length * (shell_h - FOUNDATION_H - SLAB_T), "m2",
                     "Изолационна обвивка 0.00…0.12 м навън · outward shell")
        if _has_rows(crows, bid, "facade") or _has_rows(crows, bid, "finish"):
            f_band = _outring(poly, 0.16, notes, f"{bid} finish band")
            if f_band is not None and ins_band is not None:
                try:
                    inn = poly.buffer(0.12)
                    f_band = f_band.difference(inn.intersection(f_band))
                    if f_band.geom_type == "MultiPolygon":
                        f_band = max(f_band.geoms, key=lambda g: g.area)
                    if f_band.is_empty or f_band.geom_type != "Polygon":
                        f_band = None
                except Exception:
                    f_band = None
            if f_band is not None:
                o = emit(f"{bid}.finish.l0", "Довършителна фасада", "facade finish", "WALL_EXT", "PAINT",
                         "finishes", _geom_of(f_band, FOUNDATION_H + SLAB_T, shell_h),
                         _worst(core_state, "INFERRED"), _refs(*(fp_refs + [("pack.boq", "vocab:facade|finish")])),
                         "facade", f_band.exterior.length * (shell_h - FOUNDATION_H - SLAB_T), "m2",
                         "Финен довършителен слой 0.12…0.16 м навън · finish shell")
                register(bid, "finish", o["id"])
        if _has_rows(crows, bid, "mep") or _has_rows(crows, bid, "water", shared=False) \
                or _has_rows(crows, bid, "sewer", shared=False):
            cpt = poly.representative_point()
            o = emit(f"{bid}.mep.l0", "Инсталации (символично)", "MEP riser (symbolic)", "MEP", "INSTALL",
                     "mep", {"type": "box", "center_m": [_r6(cpt.x), _r6(cpt.y), _r6(FOUNDATION_H + shell_h / 2)],
                             "size_m": [1.4, 1.4, _r6(shell_h - FOUNDATION_H)]},
                     _worst(core_state, "INFERRED"), _refs(*(fp_refs + [("pack.boq", "vocab:mep")])), "mep",
                     None, None, "Символичен обем на инсталациите — НЕ е мащабирана инсталация (INFERRED)")
            register(bid, "water", o["id"])
            register(bid, "sewer", o["id"])
    return shell_h


def _compile_site(ev, ctx, objects, register):
    """Plot-level objects in the shared frame: roads (ribbon extrudes), pipes
    (tubes below grade), the site boundary slab and a BOQ-proven fence."""
    crows, notes, reno = ctx["crows"], ctx["notes"], ctx["reno"]
    srcs = set()
    nets = ev.get("networks")
    if isinstance(nets, list) and nets:
        for ni, net in enumerate(nets):
            if not isinstance(net, dict):
                continue
            pts = net.get("polyline_m")
            if not isinstance(pts, list) or len(pts) < 2:
                continue
            kind = str(net.get("kind") or "network")
            oid = f"site-1.net-{ni}"
            refs = _refs(("dxf", f"networks[{ni}] layer {net.get('layer') or ''}"))
            if kind == "road":
                ribbon = None
                try:
                    ribbon = LineString([(float(p[0]), float(p[1])) for p in pts]).buffer(
                        1.75, cap_style=2, join_style=2)
                    if ribbon.geom_type == "MultiPolygon":
                        ribbon = max(ribbon.geoms, key=lambda g: g.area)
                        notes.append(f"{oid}: road ribbon split; largest piece kept")
                    if ribbon.is_empty or ribbon.geom_type != "Polygon":
                        ribbon = None
                except Exception as ex:
                    notes.append(f"{oid}: road ribbon rejected ({ex}); tube fallback")
                    ribbon = None
                if ribbon is not None:
                    geom = _geom_of(ribbon, GROUND, 0.08)
                    qty, unit = ribbon.area, "m2"
                else:
                    geom = {"type": "tube", "points_m": [[_r6(p[0]), _r6(p[1]), 0.04] for p in pts],
                            "radius_m": 1.75}
                    qty, unit = None, None
                o = _obj(oid, "Алея · road", "ROAD", "CONSTRUCT", "site", geom, "INFERRED", refs, [],
                         qty, unit,
                         "Алея/път по ос от чертежа, широчина 3.5 м символична (не е наблюдавана) · road ribbon, INFERRED width",
                         parent="bldg-1")
                objects.append(o)
                register("site", "road", oid)
            else:
                cat = {"water": "water", "sewer": "sewer"}.get(kind, "mep")
                o = _obj(oid, f"Мрежа {kind} · {kind} network", "PIPE", "INSTALL", "mep",
                         {"type": "tube", "points_m": [[_r6(p[0]), _r6(p[1]), -0.40] for p in pts],
                          "radius_m": 0.15},
                         "INFERRED", refs, [], None, None,
                         f"Мрежа {kind} (Ø символен, −0.40 м) · network pipe, INFERRED", parent="bldg-1")
                objects.append(o)
                register("site", cat, oid)
    site = ev.get("site_boundary")
    if isinstance(site, dict):
        sp, sst, ssrc = _fp_from_value(site, notes, "site_boundary")
        if sp is not None:
            o = _obj("bldg-1.site.l0", "Парцел · site boundary", "SITE", "EXISTING", "site",
                     _geom_of(sp, GROUND, 0.05), sst, _refs((ssrc, "site_boundary")), [],
                     sp.area, "m2", "Граница на парцела 0.00…0.05 м · site boundary (EXISTING)")
            objects.append(o)
            register("site", "site", o["id"])
            if ssrc:
                srcs.add(ssrc)
            if _has_rows(crows, "site", "fence") and not reno:
                ring_pts = [[_r6(x), _r6(y)] for x, y in sp.exterior.coords[:-1]]
                ring_pts.append(list(ring_pts[0]))
                o = _obj("site-1.fence.l0", "Ограда · site fence", "FENCE", "CONSTRUCT", "site",
                         {"type": "tube", "points_m": [[p[0], p[1], 0.9] for p in ring_pts], "radius_m": 0.12},
                         _worst(sst, "INFERRED"), _refs((ssrc, "site_boundary loop as fence")), [],
                         sp.exterior.length, "m", "Ограда по границата (височина 1.8 м символична — INFERRED)")
                objects.append(o)
                register("site", "fence", o["id"])
    return srcs


def _allocate_costs(objects, crows, targets, bids):
    """Attach every priced BOQ row to scene objects exactly once. A row scoped
    to a building lands on that building's objects of its category (else its
    root); a shared fabric row splits evenly across buildings; plot rows land on
    site objects. Sibling targets split proportionally to compiled quantity
    (even when quantities are absent). Sum of shares per row == 1; rows with no
    target stay in `unallocated` (never silently dropped)."""
    by_id = {o["id"]: o for o in objects}
    roots = {b: b for b in bids if b in by_id}
    site_obj = "bldg-1.site.l0" if "bldg-1.site.l0" in by_id else None
    alloc = {oid: {} for oid in by_id}          # oid -> {row key: share}
    by_bldg = {b: 0.0 for b in bids}
    unalloc, allocated, shared_eur, matched = [], 0.0, 0.0, 0

    def split(oids, weight):
        ids = [i for i in oids if i in by_id]
        if not ids:
            return {}
        qs = [by_id[i].get("quantity") for i in ids]
        if all(isinstance(q, (int, float)) and q > 0 for q in qs):
            tot = sum(qs)
            return {i: weight * q / tot for i, q in zip(ids, qs)}
        return {i: weight / len(ids) for i in ids}

    for r in crows:
        key, scope, cat = r["key"], r["scope"], r["cat"]
        shares = {}
        if scope == "shared":
            if cat in _FABRIC_CATS:
                per = [(b, targets.get((b, cat)) or [roots[b]]) for b in bids if b in roots]
                for b, tg in per:
                    shares.update(split(tg, 1.0 / len(per)))
            else:
                tg = targets.get(("site", cat)) or ([site_obj] if site_obj else [])
                shares = split(tg, 1.0)
        else:
            tg = targets.get((scope, cat)) or targets.get(("site", cat)) \
                or ([roots[scope]] if scope in roots else [])
            shares = split(tg, 1.0)
        for oid, sh in shares.items():
            alloc[oid][key] = _r6(alloc[oid].get(key, 0.0) + sh)
        if shares:
            matched += 1
        if r["sum"] is None:
            continue
        if shares:
            allocated += r["sum"]
            for oid, sh in shares.items():
                b = by_id[oid]["id"].split(".")[0]
                if b in by_bldg:
                    by_bldg[b] += r["sum"] * sh
            if scope == "shared":
                shared_eur += r["sum"]
        else:
            unalloc.append({"key": key, "scope": scope, "category": cat, "sum_eur": _r6(r["sum"])})
    sums = {r["key"]: r["sum"] for r in crows if r["sum"] is not None}
    for o in objects:
        a = alloc[o["id"]]
        o["boq_keys"] = sorted(a)
        o["inspect"]["cost_share"] = {k: a[k] for k in sorted(a)}
        priced = [k for k in a if k in sums]
        if priced:
            o["inspect"]["cost_eur"] = _r6(sum(sums[k] * a[k] for k in priced))
    total = sum(sums.values())
    return {"boq_total_eur": _r6(total), "allocated_eur": _r6(allocated),
            "unallocated_eur": _r6(sum(u["sum_eur"] for u in unalloc)),
            "unallocated": unalloc, "shared_eur": _r6(shared_eur),
            "by_building": {b: _r6(v) for b, v in sorted(by_bldg.items())},
            "rows_matched": matched, "rows_total": len(crows)}


def compile_scene(evidence, pack, elements=None):
    """Compile the fused evidence into a tenderops.spatial_scene.v2 dict (contract §5).
    Pure and deterministic; see module docstring for the stacking conventions."""
    ev = evidence if isinstance(evidence, dict) else {}
    pk = pack if isinstance(pack, dict) else {}
    rd = readiness(ev, pk, None)  # graph is not an input of compile_scene; GEOREFERENCED
    notes = []                    # needs >= MEASURED anyway -> suppress flag unaffected
    reno = _is_renovation(pk)
    rows = [r for r in (pk.get("boq") or []) if isinstance(r, dict)]

    specs = _building_specs(ev, rd, notes)
    bids = [s["bid"] for s in specs]
    crows = _classify_rows(rows, [(s["bid"], s["tag"]) for s in specs], reno)
    targets = {}

    def register(scope, cat, oid):
        targets.setdefault((scope, cat), []).append(oid)

    ctx = {"reno": reno, "multi": len(specs) > 1, "crows": crows, "notes": notes}
    objects = []
    all_src = set()
    primary_storeys = specs[0]["storeys"] if specs else 1
    shell_h = 0.0

    if not specs:
        # SCHEMATIC: one neutral box per BOQ container; a row reaches ONLY the box
        # of its own section title (no geometry to distribute over otherwise)
        objects.extend(_schematic_boxes(ev, pk, elements, notes))
        titles = {}
        for o in objects:
            title = o["label"].split(" · ", 1)[-1]
            titles.setdefault(title, o["id"])
            register("bldg-1", "box:" + title, o["id"])
        crows = _classify_rows(rows, [("bldg-1", 1)], reno)
        for r, row in zip(crows, rows):
            t = str(row.get("section") or "").strip()
            r["scope"], r["cat"] = "bldg-1", ("box:" + t if t in titles else "unplaced")
        bids = ["bldg-1"]
    else:
        for spec in specs:
            all_src.update(spec["srcs"])
            h = _compile_building(spec, ctx, objects, register)
            if spec["bid"] == "bldg-1":
                shell_h = h
        all_src |= _compile_site(ev, ctx, objects, register)
        glb = _glb_fact(ev)
        if glb is not None:
            minx, miny, maxx, maxy = specs[0]["poly"].bounds
            b = glb["bounds_m"] or {"min": [_r6(minx), _r6(miny), GROUND],
                                    "max": [_r6(maxx), _r6(maxy), _r6(shell_h)]}
            objects.append(_obj(
                "bldg-1.glb.l0", "Измерен модел · measured GLB model", "STRUCTURE",
                "EXISTING" if reno else "CONSTRUCT", "structure",
                {"type": "glb", "cache_key": glb["cache_key"], "bounds_m": b},
                "OBSERVED", _refs((glb["cache_key"], "glb")), [], None, None,
                "Измерена/моделна геометрия (GLB passthrough) · measured model"))
            all_src.add(glb["cache_key"])

    # invariant 4: object cap — openings are decoration-first droppable
    if len(objects) > MAX_OBJECTS:
        keep = [o for o in objects if not o["semantic_type"].startswith("OPENING_")]
        ops = [o for o in objects if o["semantic_type"].startswith("OPENING_")]
        keep = keep + ops[:max(0, MAX_OBJECTS - len(keep))]
        cut = len(objects) - min(len(objects), len(keep))
        objects = keep[:MAX_OBJECTS]
        notes.append(f"object cap {MAX_OBJECTS}: degraded, {cut} object(s) omitted (openings first)")

    # click-to-cost: every priced row is attached exactly once (shares sum to 1);
    # pack rows are the commercial truth; absent price -> cost stays None (honest).
    cost_alloc = _allocate_costs(objects, crows, targets, bids)
    if cost_alloc["unallocated"]:
        notes.append(f"cost allocation: {len(cost_alloc['unallocated'])} priced row(s) without a "
                     f"scene target ({cost_alloc['unallocated_eur']:.2f} EUR) left unallocated")
    if cost_alloc["shared_eur"] > 0 and len(bids) > 1:
        notes.append(f"cost allocation: {cost_alloc['shared_eur']:.2f} EUR of unmarked rows "
                     f"split evenly across {len(bids)} buildings (no per-building marker)")

    # parent/children wiring: every building root collects its own subtree;
    # non-rooted shared objects (site/roads/pipes) attach to bldg-1 when present
    ids = [o["id"] for o in objects]
    root_ids = [o["id"] for o in objects
                if o["parent"] is None and o["id"].startswith("bldg-")]
    primary = "bldg-1" if "bldg-1" in root_ids else None
    for rid in root_ids:
        root_obj = next(o for o in objects if o["id"] == rid)
        root_obj["children"] = [i for i in ids
                                if i.startswith(rid + ".") and i != rid]
    if primary:
        prim = next(o for o in objects if o["id"] == primary)
        owned = {i for rid in root_ids for i in [c for c in ids if c.startswith(rid + ".")]}
        owned |= set(root_ids)
        prim["children"] = [c for c in prim["children"]] + \
            [i for i in ids if i not in owned and i != primary]

    # bounds over every geometry (6dp)
    xs, ys, zs = [], [], []
    for o in objects:
        g = o["geometry"]
        t = g.get("type")
        if t in ("extrude", "gable_roof"):
            for x, y in g["polygon_m"]:
                xs.append(x); ys.append(y)
            if t == "extrude":
                zs.extend([g["z0_m"], g["z1_m"]])
            else:
                zs.extend([g["eave_z_m"], g["ridge_z_m"]])
        elif t == "box":
            cx, cy, cz = g["center_m"]; sx, sy, sz = g["size_m"]
            xs.extend([cx - sx / 2, cx + sx / 2]); ys.extend([cy - sy / 2, cy + sy / 2])
            zs.extend([cz - sz / 2, cz + sz / 2])
        elif t == "tube":
            r = float(g.get("radius_m") or 0.0)
            for x, y, z in g["points_m"]:
                xs.extend([x - r, x + r]); ys.extend([y - r, y + r]); zs.extend([z - r, z + r])
        elif t == "glb" and isinstance(g.get("bounds_m"), dict):
            b = g["bounds_m"]
            xs.extend([b["min"][0], b["max"][0]]); ys.extend([b["min"][1], b["max"][1]])
            zs.extend([b["min"][2], b["max"][2]])
    bounds = {"min": [_r6(min(xs)), _r6(min(ys)), _r6(min(zs))],
              "max": [_r6(max(xs)), _r6(max(ys)), _r6(max(zs))]} if xs else \
             {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}

    reasons = _reason(rd, ev, primary_storeys)
    if reno and rd != "NONE":
        reasons += " | ремонтна граматика: EXISTING + REPAIR/INSULATE/PAINT (invariant 6)"
    frame = ev.get("frame")
    buildings_out = [{"id": s["bid"], "tag": s["tag"], "tag_state": s["tag_state"],
                      "tag_basis": s["tag_basis"], "label": f"{_letters(s['tag'])[0]} · {_letters(s['tag'])[1]}",
                      "storeys": s["storeys"], "floor_height_m": _r6(s["fh"]),
                      "roof": (s["roof"]["type"] if s["roof"] else "unknown")} for s in specs]
    return {
        "schema": SCHEMA,
        "units": "m",
        "crs": "LOCAL_PROJECT_SPACE",
        "coord_note": "X east, Y north, Z up; metres",
        "ground_elevation_m": GROUND,
        "readiness": rd,
        "readiness_reason": reasons,
        "suppress_legacy_art": rd in _SUPPRESS_SET,  # invariant 3
        "bounds_m": bounds,
        "phases": list(PHASES),
        "objects": objects,
        "buildings": buildings_out,
        "frame": ({k: (list(v) if isinstance(v, list) else v) for k, v in frame.items()}
                  if isinstance(frame, dict) else None),
        "cost_allocation": cost_alloc,
        "evidence_ref": {"present": bool(objects), "sources": sorted(all_src)},
        "notes": notes,
    }
