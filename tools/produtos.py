"""
Ferramentas de produtos / estoque inteligente (Sessão 11).

A Tassia registra COMPRAS. O consumo é inferido do intervalo entre recompras
(em atendimentos — eixo principal — e em dias — secundário). A previsão de
reposição aprende sozinha e fica mais confiável a cada recompra.
"""
from datetime import date
from database import get_client


def registrar_compra(
    produto: str,
    preco_unitario: float,
    quantidade: float = 1,
    loja: str = None,
    data_compra: str = None,
    unidade: str = None,
) -> dict:
    """
    Registra a compra de um produto. Cria o produto no catálogo se não existir.
    - produto: nome (ex: 'gel', 'esmalte vermelho')
    - preco_unitario: preço por unidade
    - quantidade: quantas unidades (default 1). 3 potes = quantidade 3.
    - loja: onde comprou (pra comparar preços depois)
    - data_compra: YYYY-MM-DD (default hoje)
    - unidade: tipo de unidade (pote, frasco...) — opcional
    """
    db = get_client()
    # upsert do produto
    up = db.rpc("upsert_produto", {"p_nome": produto, "p_unidade": unidade}).execute()
    produto_id = up.data[0]["id"]

    payload = {
        "produto_id": produto_id,
        "data": data_compra or date.today().isoformat(),
        "quantidade": quantidade,
        "preco_unitario": preco_unitario,
        "loja": loja,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    db.table("compras").insert(payload).execute()

    total = quantidade * preco_unitario
    return {"ok": True, "produto": produto, "quantidade": quantidade,
            "preco_unitario": preco_unitario, "total": total, "loja": loja}


def _produto_id(nome: str):
    db = get_client()
    resp = db.rpc("buscar_produto", {"p_nome": nome}).execute()
    return resp.data or []


def previsao_produto(produto: str) -> dict:
    """
    Mostra a previsão de reposição de um produto: quanto costuma durar (em
    atendimentos e em dias por unidade), quanto já foi consumido desde a última
    compra, e o nível de confiança da previsão.
    """
    matches = _produto_id(produto)
    if not matches:
        return {"ok": False, "erro": "produto não encontrado"}
    if len(matches) > 1:
        return {"ok": False, "varios": [m["nome"] for m in matches],
                "obs": "achei mais de um — especifique qual"}
    db = get_client()
    resp = db.rpc("previsao_produto", {"p_produto_id": matches[0]["id"]}).execute()
    if not resp.data:
        return {"ok": False, "erro": "sem dados"}
    r = resp.data[0]
    return {
        "ok": True,
        "produto": r["nome"],
        "compras": int(r["compras_registradas"]),
        "ciclos": int(r["ciclos_fechados"]),
        "confianca": r["confianca"],
        "dura_atendimentos_por_unidade": float(r["media_atend_por_unidade"]) if r["media_atend_por_unidade"] else None,
        "dura_dias_por_unidade": float(r["media_dias_por_unidade"]) if r["media_dias_por_unidade"] else None,
        "ultima_compra": r["ultima_compra"],
        "atendimentos_desde_ultima": int(r["atend_desde_ultima"]) if r["atend_desde_ultima"] is not None else 0,
        "dias_desde_ultima": int(r["dias_desde_ultima"]) if r["dias_desde_ultima"] is not None else 0,
        "consumo_estimado_pct": float(r["consumo_estimado_pct"]) if r["consumo_estimado_pct"] is not None else None,
    }


def gasto_produto(produto: str) -> dict:
    """Quanto já foi gasto com um produto no total, e em quantas compras."""
    matches = _produto_id(produto)
    if not matches:
        return {"ok": False, "erro": "produto não encontrado"}
    if len(matches) > 1:
        return {"ok": False, "varios": [m["nome"] for m in matches]}
    db = get_client()
    resp = db.rpc("gasto_produto", {"p_produto_id": matches[0]["id"]}).execute()
    r = resp.data[0]
    return {"ok": True, "produto": matches[0]["nome"],
            "total_gasto": float(r["total_gasto"]),
            "total_unidades": float(r["total_unidades"]),
            "compras": int(r["compras"])}


def precos_por_loja(produto: str) -> dict:
    """
    Mostra o preço do produto por loja (último e menor preço de cada lugar).
    Use quando a Tassia for comprar e quiser saber onde está mais barato.
    """
    matches = _produto_id(produto)
    if not matches:
        return {"ok": False, "erro": "produto não encontrado"}
    if len(matches) > 1:
        return {"ok": False, "varios": [m["nome"] for m in matches]}
    db = get_client()
    resp = db.rpc("precos_por_loja", {"p_produto_id": matches[0]["id"]}).execute()
    lojas = [
        {"loja": r["loja"], "ultimo_preco": float(r["ultimo_preco"]),
         "menor_preco": float(r["menor_preco"]), "ultima_compra": r["ultima_compra"]}
        for r in (resp.data or [])
    ]
    if not lojas:
        return {"ok": True, "produto": matches[0]["nome"], "lojas": [],
                "obs": "sem histórico de compras"}
    return {"ok": True, "produto": matches[0]["nome"], "lojas": lojas}


def listar_produtos() -> dict:
    """Lista todos os produtos cadastrados."""
    db = get_client()
    resp = db.table("produtos").select("nome, unidade").eq("ativo", True).order("nome").execute()
    itens = resp.data or []
    if not itens:
        return {"total": 0, "produtos": [], "obs": "nenhum produto cadastrado"}
    return {"total": len(itens), "produtos": itens}
