import os
import sys
import unittest
from unittest.mock import MagicMock

# Create mock modules for external dependencies so tests can run in standard python
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

class Milestone1SecurityTests(unittest.TestCase):
    def test_mongodb_uri_security(self):
        from app.mongodb import get_mongo_uri
        import app.config
        
        orig_uri = os.environ.get("MONGODB_URI")
        orig_url = os.environ.get("MONGO_URL")
        orig_settings_uri = getattr(app.config.settings, "MONGODB_URI", None)
        
        try:
            if "MONGODB_URI" in os.environ:
                del os.environ["MONGODB_URI"]
            if "MONGO_URL" in os.environ:
                del os.environ["MONGO_URL"]
            if hasattr(app.config.settings, "MONGODB_URI"):
                app.config.settings.MONGODB_URI = ""
            
            with self.assertRaises(RuntimeError) as ctx:
                get_mongo_uri()
            self.assertIn("MONGODB_URI environment variable is required", str(ctx.exception))
            
            os.environ["MONGODB_URI"] = "mongodb+srv://admin:rotatedpassword@cluster0.mongodb.net/testdb"
            self.assertEqual(get_mongo_uri(), "mongodb+srv://admin:rotatedpassword@cluster0.mongodb.net/testdb")
        finally:
            if orig_uri is not None:
                os.environ["MONGODB_URI"] = orig_uri
            elif "MONGODB_URI" in os.environ:
                del os.environ["MONGODB_URI"]
            if orig_url is not None:
                os.environ["MONGO_URL"] = orig_url
            elif "MONGO_URL" in os.environ:
                del os.environ["MONGO_URL"]
            if orig_settings_uri is not None:
                app.config.settings.MONGODB_URI = orig_settings_uri

    def test_auth_roles_and_protection(self):
        from app.auth import (
            AuthRole,
            AuthenticatedUser,
            require_operator,
            require_project_access,
        )
        from fastapi import HTTPException

        # 1. Operator
        op_user = AuthenticatedUser(role=AuthRole.OPERATOR, identifier="operator")
        self.assertEqual(require_operator(op_user), op_user)
        self.assertEqual(require_project_access("any_proj", op_user), op_user)

        # 2. Public
        pub_user = AuthenticatedUser(role=AuthRole.PUBLIC, identifier="public")
        with self.assertRaises(HTTPException) as ctx:
            require_operator(pub_user)
        self.assertEqual(ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as ctx:
            require_project_access("proj_123", pub_user)
        self.assertEqual(ctx.exception.status_code, 401)

        # 3. Creator
        creator_user = AuthenticatedUser(
            role=AuthRole.CREATOR,
            identifier="proj_123",
            project_id="proj_123",
            slug="cal-os",
            creator_handle="calnewport",
        )
        self.assertEqual(require_project_access("proj_123", creator_user), creator_user)
        self.assertEqual(require_project_access("cal-os", creator_user), creator_user)
        self.assertEqual(require_project_access("@calnewport", creator_user), creator_user)

        with self.assertRaises(HTTPException) as ctx:
            require_project_access("other_project", creator_user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_public_project_safe_filter(self):
        from app.routers.projects import _filter_public_project_dict

        internal_doc = {
            "id": "proj_abc",
            "slug": "brain-flow",
            "productName": "Brain Flow",
            "productTagline": "Deep focus tracker",
            "creatorEmail": "creator@private.com",
            "email": "creator@private.com",
            "telemetry": {
                "views": 420,
                "visitors": 120,
                "reservations": [{"name": "Private Buyer", "amount": 100, "email": "buyer@secret.com"}],
                "presalesRevenue": 5000,
            },
            "gateDecisions": [{"notes": "Internal executive notes"}],
            "metadataInfo": {"activity_logs": [{"secret": "data"}]},
            "brandColor": "#10B981",
            "customer": "Knowledge workers",
            "problem": "Context switching",
            "keyFeatures": ["Stage 1", "Stage 2"],
            "pricing": "$19/mo",
        }

        filtered = _filter_public_project_dict(internal_doc)
        self.assertEqual(filtered["id"], "proj_abc")
        self.assertEqual(filtered["slug"], "brain-flow")
        self.assertEqual(filtered["productName"], "Brain Flow")
        self.assertNotIn("creatorEmail", filtered)
        self.assertNotIn("email", filtered)
        self.assertNotIn("telemetry", filtered)
        self.assertNotIn("reservations", filtered)
        self.assertNotIn("gateDecisions", filtered)
        self.assertNotIn("metadataInfo", filtered)

if __name__ == "__main__":
    unittest.main()
