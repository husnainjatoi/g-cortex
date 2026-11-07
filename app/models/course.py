from sqlalchemy import Column, String, Integer, ForeignKey, Table
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.database import Base

# Association table for the Directed Acyclic Graph (DAG) of prerequisites
course_prerequisites = Table(
    "course_prerequisites",
    Base.metadata,
    Column("course_code", String(10), ForeignKey("courses.course_code"), primary_key=True),
    Column("prereq_code", String(10), ForeignKey("courses.course_code"), primary_key=True)
)

class Course(Base):
    __tablename__ = "courses"
    
    course_code = Column(String(10), primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    credits = Column(Integer, nullable=False)
    
    # Vector column for semantic matching in interest-based elective recommendations
    policy_embedding = Column(Vector(1536)) 

    # Self-referential many-to-many relationship to map the prerequisite DAG[cite: 1]
    prerequisites = relationship(
        "Course",
        secondary=course_prerequisites,
        primaryjoin=course_code==course_prerequisites.c.course_code,
        secondaryjoin=course_code==course_prerequisites.c.prereq_code,
        backref="required_by"
    )