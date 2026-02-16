"""
Notion API client — campaign brief intake for The Forge.
"""
import re
import requests as http_requests
import config
from services.retry import retry_request


class NotionClient:
    BASE = "https://api.notion.com/v1"

    def __init__(self, api_key):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

    # Default timeout: (connect=10s, read=120s)
    DEFAULT_TIMEOUT = (10, 120)

    def _get(self, path, params=None):
        r = retry_request(
            lambda: http_requests.get(
                f"{self.BASE}{path}", headers=self.headers, params=params,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"Notion GET {path}",
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload):
        r = retry_request(
            lambda: http_requests.post(
                f"{self.BASE}{path}", headers=self.headers, json=payload,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"Notion POST {path}",
        )
        r.raise_for_status()
        return r.json()

    def list_campaigns(self):
        """List campaign pages under Active & Planned Campaigns."""
        results = []
        data = self._get(f"/blocks/{config.NOTION_CAMPAIGNS_PAGE_ID}/children")
        for block in data.get("results", []):
            if block.get("type") == "child_page":
                title = block.get("child_page", {}).get("title", "Untitled")
                results.append({"id": block["id"], "title": title})
        # Fallback: search for pages with parent = campaigns page
        if not results:
            data = self._post("/search", {
                "filter": {"property": "object", "value": "page"},
                "query": "",
            })
            for page in data.get("results", []):
                parent = page.get("parent", {})
                if parent.get("page_id", "").replace("-", "") == config.NOTION_CAMPAIGNS_PAGE_ID.replace("-", ""):
                    title_parts = page.get("properties", {}).get("title", {}).get("title", [])
                    title = "".join(t.get("plain_text", "") for t in title_parts) or "Untitled"
                    results.append({"id": page["id"], "title": title})
        return results

    def get_page_blocks(self, page_id):
        """Recursively fetch all blocks for a page."""
        all_blocks = []
        start_cursor = None
        while True:
            params = {}
            if start_cursor:
                params["start_cursor"] = start_cursor
            data = self._get(f"/blocks/{page_id}/children", params)
            blocks = data.get("results", [])
            all_blocks.extend(blocks)
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
        return all_blocks

    def get_campaign_brief(self, page_id):
        """Fetch and parse a campaign brief into structured data."""
        blocks = self.get_page_blocks(page_id)
        brief = {
            "raw_blocks": blocks,
            "sections": {},
            "icp": "",
            "personas": [],
            "company_characteristics": "",
            "value_proposition": "",
            "messaging_pillars": "",
            "keywords": [],
            "target_titles": [],
            "target_domains": [],
        }

        current_section = None
        current_content = []

        for block in blocks:
            btype = block.get("type", "")

            # Extract text from rich text blocks
            text = ""
            if btype in ("heading_1", "heading_2", "heading_3"):
                rich_texts = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_texts)
                if current_section:
                    brief["sections"][current_section] = "\n".join(current_content)
                current_section = text.strip()
                current_content = []
                continue
            elif btype in ("paragraph", "bulleted_list_item", "numbered_list_item", "toggle"):
                rich_texts = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_texts)

            if text and current_section:
                current_content.append(text)

        if current_section:
            brief["sections"][current_section] = "\n".join(current_content)

        # Parse specific fields from sections
        for section_name, content in brief["sections"].items():
            sn_upper = section_name.upper()
            if "ICP" in sn_upper or "IDEAL CUSTOMER" in sn_upper:
                brief["icp"] = content
            elif "PERSONA" in sn_upper or "BUYER" in sn_upper:
                brief["personas"] = [p.strip() for p in content.split("\n") if p.strip()]
                for persona in brief["personas"]:
                    titles = re.findall(
                        r'(?:VP|SVP|Head|Director|Chief|Manager|Lead|C[A-Z]O)\s*(?:of\s+)?[\w\s]+',
                        persona, re.IGNORECASE,
                    )
                    brief["target_titles"].extend([t.strip() for t in titles])
            elif "COMPANY" in sn_upper and ("CHARACTERISTIC" in sn_upper or "PROFILE" in sn_upper):
                brief["company_characteristics"] = content
            elif "VALUE PROP" in sn_upper:
                brief["value_proposition"] = content
            elif "MESSAGING" in sn_upper:
                brief["messaging_pillars"] = content

        # Build keywords from ICP + company characteristics
        keyword_source = f"{brief['icp']} {brief['company_characteristics']}"
        kw_patterns = re.findall(
            r'\b(?:SaaS|B2B|enterprise|startup|mid-market|series [A-Z]|outbound|'
            r'sales|marketing|revenue|growth|automation|AI|machine learning|'
            r'fintech|healthtech|edtech|martech|security|cloud|data)\b',
            keyword_source, re.IGNORECASE,
        )
        brief["keywords"] = list(set(kw.lower() for kw in kw_patterns))

        # Extract target domains from all content
        domain_content = ""
        for section_name, content in brief["sections"].items():
            sn_upper = section_name.upper()
            if any(kw in sn_upper for kw in ("TARGET COMPAN", "TARGET ACCOUNT", "DOMAIN", "COMPANY LIST")):
                domain_content += "\n" + content

        all_text = "\n".join(brief["sections"].values())
        domain_matches = re.findall(
            r'\b([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.(?:com|io|co|ai|net|org|dev|tech|app|software|cloud|so))\b',
            all_text,
        )
        for block in blocks:
            btype = block.get("type", "")
            if btype in ("paragraph", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"):
                for rt in block.get(btype, {}).get("rich_text", []):
                    href = rt.get("href") or (rt.get("text", {}).get("link") or {}).get("url", "")
                    if href:
                        url_domain = re.search(r'https?://(?:www\.)?([^/\s?#]+)', href)
                        if url_domain:
                            domain_matches.append(url_domain.group(1))

        exclude_domains = {
            "example.com", "google.com", "notion.so", "notion.com", "slack.com",
            "github.com", "clay.com", "n8n.io", "supersend.io", "supersend.com",
            "zapier.com", "make.com", "airtable.com", "loom.com",
            "octavehq.com", "hubspot.com",
        }
        tool_roots = {"google.com", "slack.com", "notion.so", "n8n.cloud", "clay.com"}
        seen = set()
        for d in domain_matches:
            d_clean = d.lower().strip()
            if d_clean in seen or d_clean in exclude_domains:
                continue
            if any(d_clean.endswith("." + root) for root in tool_roots):
                continue
            seen.add(d_clean)
            brief["target_domains"].append(d_clean)

        return brief
