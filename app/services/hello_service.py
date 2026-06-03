"""Hello world business logic."""

from app.schemas.hello import HelloWorldResponse


class HelloService:
    """Service for hello world operations."""

    def get_hello(self) -> HelloWorldResponse:
        """Return hello world response.

        Returns:
            HelloWorldResponse with greeting message.
        """
        return HelloWorldResponse(message="Hello World")


hello_service = HelloService()
