import os
import sys
import unittest
from unittest.mock import MagicMock

# Mock external modules for test environment
mock_mods = [
    "pymongo", "pymongo.database", "pymongo.collection",
    "fastapi", "pydantic",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.orm.attributes", "sqlalchemy.pool",
    "motor", "motor.motor_asyncio", "dns", "dns.resolver",
    "cloudinary", "cloudinary.uploader",
    "fpdf", "fpdf2", "google", "google.genai",
]
for mod in mock_mods:
    if mod not in sys.modules:
        m = MagicMock()
        if mod == "pydantic":
            class MockBaseModel:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
                def dict(self):
                    return self.__dict__
            m.BaseModel = MockBaseModel
        if mod == "fastapi":
            class MockHTTPException(Exception):
                def __init__(self, status_code, detail=None):
                    self.status_code = status_code
                    self.detail = detail
                    super().__init__(f"{status_code}: {detail}")
            m.HTTPException = MockHTTPException
            m.Depends = lambda x: x
            m.Header = lambda *args, **kwargs: None
            m.Query = lambda *args, **kwargs: None
            m.Request = MagicMock
            m.APIRouter = MagicMock
        if mod == "sqlalchemy.orm.attributes":
            m.flag_modified = MagicMock()
        sys.modules[mod] = m

class MockCoLaunchProject:
    pass

class MockValidationPlan:
    pass

class MockValidationCampaign:
    pass

class MockValidationTelemetry:
    pass

class MockValidationGateDecision:
    pass

class MockCreator:
    pass

m_models = MagicMock()
m_models.CoLaunchProject = MockCoLaunchProject
m_models.ValidationPlan = MockValidationPlan
m_models.ValidationCampaign = MockValidationCampaign
m_models.ValidationTelemetry = MockValidationTelemetry
m_models.ValidationGateDecision = MockValidationGateDecision
sys.modules["app.models.project"] = m_models
sys.modules["app.models.creator"] = MagicMock(Creator=MockCreator)
sys.modules["app.models"] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

class Milestone2ConceptToProjectTests(unittest.TestCase):
    def test_unique_slug_generation(self):
        from app.routers.projects import _generate_unique_project_slug

        # Mock mongo collection
        mock_coll = MagicMock()
        
        # Scenario 1: No collision
        mock_coll.find_one.return_value = None
        slug1 = _generate_unique_project_slug(mock_coll, "Deep Focus Tracker", "calnewport")
        self.assertEqual(slug1, "deep-focus-tracker")

        # Scenario 2: Collision on base name, appends creator handle
        def mock_find_one_collision(query):
            cand = query.get("$or", [{}])[0].get("slug")
            if cand == "deep-focus-tracker":
                return {"id": "existing_1", "slug": "deep-focus-tracker"}
            return None

        mock_coll.find_one.side_effect = mock_find_one_collision
        slug2 = _generate_unique_project_slug(mock_coll, "Deep Focus Tracker", "calnewport")
        self.assertEqual(slug2, "deep-focus-tracker-calnewport")

        # Scenario 3: Collision on base and handle, appends number
        def mock_find_one_both_collided(query):
            cand = query.get("$or", [{}])[0].get("slug")
            if cand in ("deep-focus-tracker", "deep-focus-tracker-calnewport"):
                return {"id": "existing", "slug": cand}
            return None

        mock_coll.find_one.side_effect = mock_find_one_both_collided
        slug3 = _generate_unique_project_slug(mock_coll, "Deep Focus Tracker", "calnewport")
        self.assertEqual(slug3, "deep-focus-tracker-2")

    def test_execute_create_co_launch_project_merged_schema(self):
        from app.routers.projects import execute_create_co_launch_project, CreateProjectRequest
        import app.mongodb

        saved_docs = []
        mock_coll = MagicMock()
        mock_coll.find_one.return_value = None
        mock_coll.replace_one.side_effect = lambda q, doc, upsert=False: saved_docs.append(doc)

        orig_get_collection = app.mongodb.get_collection
        app.mongodb.get_collection = lambda name: mock_coll if name == "co_launch_projects" else MagicMock()

        try:
            concept = {
                "name": "FocusOS",
                "tagline": "Eliminate distraction and organize deep work sprints",
                "problem": "Knowledge workers lose 4 hours a day to context switching",
                "keyFeatures": ["Time blocker", "Context barrier", "Daily deep review"],
                "pricing": "$29/mo Starter • $79/mo Pro",
                "customer": "Academic researchers and deep work professionals",
                "audienceEvidence": "I constantly get derailed by Slack and email.",
                "revenueModel": "Subscription · 50/50 revenue share",
                "mvpDifficulty": "2 weeks",
                "opportunityScore": 95,
                "brandColor": "#10B981",
            }

            req = CreateProjectRequest(
                creatorId="creator_cal",
                creatorHandle="calnewport",
                creatorName="Cal Newport",
                creatorEmail="cal@studyhacks.com",
                followers="250,000",
                selectedConcept=concept,
                pricing=concept["pricing"],
                productName=concept["name"],
                productTagline=concept["tagline"],
            )

            result = execute_create_co_launch_project(db=MagicMock(), body=req)

            # 1. Merged schema checks
            self.assertEqual(result["productName"], "FocusOS")
            self.assertEqual(result["productTagline"], "Eliminate distraction and organize deep work sprints")
            self.assertEqual(result["customer"], "Academic researchers and deep work professionals")
            self.assertEqual(result["problem"], "Knowledge workers lose 4 hours a day to context switching")
            self.assertEqual(result["keyFeatures"], ["Time blocker", "Context barrier", "Daily deep review"])
            self.assertEqual(result["audienceEvidence"], "I constantly get derailed by Slack and email.")
            self.assertEqual(result["revenueModel"], "Subscription · 50/50 revenue share")

            # 2. Frozen snapshot checks
            self.assertTrue(result["conceptFrozen"])
            self.assertIsNotNone(result["selectedConceptFrozenAt"])
            self.assertTrue(result["selectedConcept"].get("isFrozen"))
            self.assertEqual(result["selectedConcept"]["name"], "FocusOS")

            # 3. Unique slug assigned
            self.assertEqual(result["slug"], "focusos")
            self.assertEqual(result["productSlug"], "focusos")

            # 4. Computed threshold checks (deterministic from pricing, not made up)
            self.assertGreater(result["presaleTarget"], 0)
            val_plan = result["validationPlan"]
            self.assertEqual(val_plan["status"], "ready")
            self.assertTrue(val_plan["generated"])
            self.assertIn("in presales", val_plan["threshold"])
            self.assertEqual(val_plan["customer"], concept["customer"])
            self.assertEqual(val_plan["problem"], concept["problem"])

            # 5. Dual write keys
            self.assertEqual(result["currentPhase"], 1)
            self.assertEqual(result["current_phase"], 1)
            self.assertEqual(result["status"], "validating")

            # 6. No fake waitlist
            camp = result.get("validationCampaign") or {}
            infra = camp.get("infrastructure") or {}
            self.assertEqual(infra.get("waitlistCount"), 0)

        finally:
            app.mongodb.get_collection = orig_get_collection

    def test_locked_project_protection(self):
        from app.routers.projects import execute_create_co_launch_project, CreateProjectRequest
        import app.mongodb

        existing_locked_project = {
            "_id": "proj_existing_123",
            "id": "proj_existing_123",
            "creatorId": "creator_cal",
            "creatorHandle": "calnewport",
            "productName": "Original Venture",
            "slug": "original-venture",
            "currentPhase": 1,
            "status": "validating",
            "isLocked": True,
            "telemetry": {
                "visitors": 142,
                "reservations": [{"id": "res_1", "amount": 100, "status": "paid"}],
                "presalesRevenue": 100.0,
            },
        }

        mock_coll = MagicMock()
        mock_coll.find_one.return_value = existing_locked_project

        orig_get_collection = app.mongodb.get_collection
        app.mongodb.get_collection = lambda name: mock_coll if name == "co_launch_projects" else MagicMock()

        try:
            req = CreateProjectRequest(
                id="proj_existing_123",
                creatorId="creator_cal",
                creatorHandle="calnewport",
                productName="Overwritten Venture",
                selectedConcept={"name": "Overwritten Venture"},
            )

            result = execute_create_co_launch_project(db=MagicMock(), body=req)

            # Locked project MUST NOT be overwritten
            self.assertEqual(result["id"], "proj_existing_123")
            self.assertEqual(result["productName"], "Original Venture")
            self.assertEqual(result["slug"], "original-venture")
            self.assertEqual(len(result["telemetry"]["reservations"]), 1)
            mock_coll.replace_one.assert_not_called()

        finally:
            app.mongodb.get_collection = orig_get_collection

if __name__ == "__main__":
    unittest.main()
