from seedance_role_scene_remake.assets import AssetsClient


def test_assets_client_includes_project_name_in_calls(monkeypatch):
    calls = []

    def fake_call(self, action, body):
        calls.append((action, body))
        if action == "CreateAssetGroup":
            return {"Result": {"Id": "group-1"}}
        if action == "CreateAsset":
            return {"Result": {"Id": "asset-1"}}
        if action == "GetAsset":
            return {"Result": {"Id": "asset-1", "Status": "Active", "AssetType": "Video", "ProjectName": "proj"}}
        if action == "ListAssets":
            return {"Result": {"Items": []}}
        return {"Result": {"Items": []}}

    monkeypatch.setattr(AssetsClient, "_call", fake_call)
    client = AssetsClient("ak", "sk", project_name="proj")

    assert client.create_asset_group("g", group_type="AIGC") == "group-1"
    assert client.create_asset("group-1", "https://example.com/a.mp4", asset_type="Video") == "asset-1"
    assert client.get_asset("asset-1")["Status"] == "Active"
    assert client.list_assets(group_ids=["group-1"], group_type="AIGC") == []

    assert calls[0][1]["ProjectName"] == "proj"
    assert calls[1][1]["ProjectName"] == "proj"
    assert calls[2][1]["ProjectName"] == "proj"
    assert calls[3][1]["ProjectName"] == "proj"
