"""parscoders adapter - placeholder until the real flow is built (owner 2026-10-29)."""
class ParscodersAdapter:
    async def run_site_flow(self, page, context, proposal_data, mode):
        return {"status": "form_error", "filled_values": {}, "error_message": "parscoders adapter not built yet"}
async def run_site_flow(page, context, proposal_data, mode):
    return await ParscodersAdapter().run_site_flow(page, context, proposal_data, mode)
