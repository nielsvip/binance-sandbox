import json

from tools import v15_nextgen_fullcell_coverage as C


def test_diagonal_covers_rows_early_and_preserves_exact_cartesian_cell_ids(tmp_path):
    path=tmp_path/'catalog.json'
    rows=[dict(tab='EXIT',row=n,switch='FLAG_'+str(n),cand=True) for n in range(3,7)]
    headers={name+'=15m':[dict(tab='EXIT',column=index+12)] for index,name in enumerate(['A','B','C'])}
    path.write_text(json.dumps(dict(categories={'CRYPTO_LONG':dict(source={'sha256':'a'*64},rows=rows,headers=headers)})))
    rowmajor=list(C.iter_cells(path,'CRYPTO_LONG','row-major'))
    diagonal=list(C.iter_cells(path,'CRYPTO_LONG','diagonal'))
    assert len(diagonal)==12
    assert len({row['row'] for row in diagonal[:4]})==4
    assert {row['catalog_cell_id'] for row in diagonal}=={row['catalog_cell_id'] for row in rowmajor}
    shards=[{row['catalog_cell_id'] for row in diagonal if int(row['catalog_cell_id'][:16],16)%4==shard} for shard in range(4)]
    assert sum(len(shard) for shard in shards)==12
    assert set.union(*shards)=={row['catalog_cell_id'] for row in rowmajor}
