# Copyright 2022-2024 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .core import Base


class User(Base):
    __tablename__ = "t_user"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account = Column(String(255), index=True, nullable=False)
    username = Column(String(255), index=True, unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False)
    last_login_ts = Column(Integer, nullable=True)
    role_id = Column(Integer, ForeignKey("t_role.id"))

    role = relationship("Role", back_populates="users")
    secrets = relationship("Secret", back_populates="user")


class Role(Base):
    __tablename__ = "t_role"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), index=True, unique=True, nullable=False)
    update_ts = Column(Integer, nullable=False)
    permissions = Column(Text, nullable=False)

    users = relationship("User", back_populates="role")


class Secret(Base):
    __tablename__ = "t_secret"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), index=True, unique=True, nullable=False)
    secrets = Column(String(255), nullable=False)
    created_ts = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey("t_user.id"), nullable=False)

    user = relationship("User", back_populates="secrets")
