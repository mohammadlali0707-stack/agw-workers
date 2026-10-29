"""freelancer adapter - placeholder until the real flow is built (owner 2026-10-29)."""
class FreelancerAdapter:
    async def run_site_flow(self, page, context, proposal_data, mode):
        return {"status": "form_error", "filled_values": {}, "error_message": "freelancer adapter not built yet"}
async def run_site_flow(page, context, proposal_data, mode):
    return await FreelancerAdapter().run_site_flow(page, context, proposal_data, mode)
