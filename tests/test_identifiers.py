from music_material_agent.searching.identifiers import valid_platform_id


def test_platform_id_shapes_block_cross_platform_anchor_ids() -> None:
    assert valid_platform_id("soda", "7494540061028878375")
    assert valid_platform_id("netease", "2696385334")
    assert valid_platform_id("qq", "0016LEhu3KaZ8H")
    assert valid_platform_id("kugou", "D9000DC0611AB96CD963CEF900F1C357")
    assert valid_platform_id("kuwo", "469012218")
    assert not valid_platform_id("kugou", "7494540061028878375")
    assert not valid_platform_id("qq", "2696385334")
