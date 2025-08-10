from pydantic import BaseModel

class UserBase(BaseModel):
    name: str
    description: str

class UserCreate(UserBase):
    pass

class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True

class WidgetBase(BaseModel):
    name: str
    description: str

class WidgetCreate(WidgetBase):
    pass

class WidgetResponse(WidgetBase):
    id: int

    class Config:
        from_attributes = True
